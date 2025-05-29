import difflib

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Field, lookups
from django.db.models.expressions import Col
from django.db.models.lookups import Lookup, Transform

from .. import forms
from ..query_utils import process_lhs, process_rhs
from . import EmbeddedModelField
from .array import ArrayField


class EmbeddedModelArrayField(ArrayField):
    ALLOWED_LOOKUPS = {
        "in",
        "exact",
        "iexact",
        "gt",
        "gte",
        "lt",
        "lte",
        "all",
        "contained_by",
    }

    def __init__(self, embedded_model, **kwargs):
        if "size" in kwargs:
            raise ValueError("EmbeddedModelArrayField does not support size.")
        super().__init__(EmbeddedModelField(embedded_model), **kwargs)
        self.embedded_model = embedded_model

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if path == "django_mongodb_backend.fields.embedded_model_array.EmbeddedModelArrayField":
            path = "django_mongodb_backend.fields.EmbeddedModelArrayField"
        kwargs["embedded_model"] = self.embedded_model
        del kwargs["base_field"]
        return name, path, args, kwargs

    def get_db_prep_value(self, value, connection, prepared=False):
        if isinstance(value, list | tuple):
            # Must call get_db_prep_save() rather than get_db_prep_value()
            # to transform model instances to dicts.
            return [self.base_field.get_db_prep_save(i, connection) for i in value]
        if value is not None:
            raise TypeError(
                f"Expected list of {self.embedded_model!r} instances, not {type(value)!r}."
            )
        return value

    def formfield(self, **kwargs):
        # Skip ArrayField.formfield() which has some differeences, including
        # unneeded "base_field" and "max_length" instead of "max_num".
        return Field.formfield(
            self,
            **{
                "form_class": forms.EmbeddedModelArrayField,
                "model": self.base_field.embedded_model,
                "max_num": self.max_size,
                "prefix": self.name,
                **kwargs,
            },
        )

    def get_transform(self, name):
        transform = super().get_transform(name)
        if transform:
            return transform
        return KeyTransformFactory(name, self)

    def get_lookup(self, name):
        return super().get_lookup(name) if name in self.ALLOWED_LOOKUPS else None


class EMFArrayBuildinLookup(Lookup):
    def check_lhs(self):
        if not isinstance(self.lhs, KeyTransform):
            raise ValueError(
                "Cannot apply this lookup directly to EmbeddedModelArrayField. "
                "Try querying one of its embedded fields instead."
            )

    def process_rhs(self, compiler, connection):
        value = self.rhs
        if not self.get_db_prep_lookup_value_is_iterable:
            value = [value]
        # Value must be serialized based on the query target.
        # If querying a subfield inside the array (i.e., a nested KeyTransform), use the output
        # field of the subfield. Otherwise, use the base field of the array itself.
        get_db_prep_value = self.lhs._lhs.output_field.get_db_prep_value
        return None, [
            v if hasattr(v, "as_mql") else get_db_prep_value(v, connection, prepared=True)
            for v in value
        ]

    def as_mql(self, compiler, connection):
        self.check_lhs()
        # Querying a subfield within the array elements (via nested KeyTransform).
        # Replicates MongoDB's implicit ANY-match by mapping over the array and applying
        # `$in` on the subfield.
        lhs_mql, inner_lhs_mql = process_lhs(self, compiler, connection)
        values = process_rhs(self, compiler, connection)
        return {
            "$anyElementTrue": {
                "$ifNull": [
                    {
                        "$map": {
                            "input": lhs_mql,
                            "as": "item",
                            "in": connection.mongo_operators[self.lookup_name](
                                inner_lhs_mql, values
                            ),
                        }
                    },
                    [],
                ]
            }
        }


@EmbeddedModelArrayField.register_lookup
class EMFArrayIn(EMFArrayBuildinLookup, lookups.In):
    pass


@EmbeddedModelArrayField.register_lookup
class EMFArrayExact(EMFArrayBuildinLookup, lookups.Exact):
    pass


@EmbeddedModelArrayField.register_lookup
class EMFArrayIExact(EMFArrayBuildinLookup, lookups.IExact):
    get_db_prep_lookup_value_is_iterable = False


@EmbeddedModelArrayField.register_lookup
class EMFArrayGreaterThan(EMFArrayBuildinLookup, lookups.GreaterThan):
    pass


@EmbeddedModelArrayField.register_lookup
class EMFArrayGreaterThanOrEqual(EMFArrayBuildinLookup, lookups.GreaterThanOrEqual):
    pass


@EmbeddedModelArrayField.register_lookup
class EMFArrayLessThan(EMFArrayBuildinLookup, lookups.LessThan):
    pass


@EmbeddedModelArrayField.register_lookup
class EMFArrayLessThanOrEqual(EMFArrayBuildinLookup, lookups.LessThanOrEqual):
    pass


@EmbeddedModelArrayField.register_lookup
class EMFArrayAll(EMFArrayBuildinLookup, Lookup):
    lookup_name = "all"
    get_db_prep_lookup_value_is_iterable = False

    def as_mql(self, compiler, connection):
        self.check_lhs()
        lhs_mql, inner_lhs_mql = process_lhs(self, compiler, connection)
        values = process_rhs(self, compiler, connection)
        return {
            "$setIsSubset": [
                values,
                {
                    "$ifNull": [
                        {
                            "$map": {
                                "input": lhs_mql,
                                "as": "item",
                                "in": inner_lhs_mql,
                            }
                        },
                        [],
                    ]
                },
            ]
        }


@EmbeddedModelArrayField.register_lookup
class ArrayContainedBy(EMFArrayBuildinLookup, Lookup):
    lookup_name = "contained_by"
    get_db_prep_lookup_value_is_iterable = False

    def as_mql(self, compiler, connection):
        lhs_mql, inner_lhs_mql = process_lhs(self, compiler, connection)
        lhs_mql = {
            "$map": {
                "input": lhs_mql,
                "as": "item",
                "in": inner_lhs_mql,
            }
        }
        value = process_rhs(self, compiler, connection)
        return {
            "$and": [
                {"$ne": [lhs_mql, None]},
                {"$ne": [value, None]},
                {"$setIsSubset": [lhs_mql, value]},
            ]
        }


class KeyTransform(Transform):
    def __init__(self, key_name, array_field, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.array_field = array_field
        self.key_name = key_name
        # The iteration items begins from the base_field, a virtual column with
        # base field output type is created.
        column_target = array_field.embedded_model._meta.get_field(key_name).clone()
        column_name = f"$item.{key_name}"
        column_target.db_column = column_name
        column_target.set_attributes_from_name(column_name)
        self._lhs = Col(None, column_target)
        self._sub_transform = None

    def __call__(self, this, *args, **kwargs):
        self._lhs = self._sub_transform(self._lhs, *args, **kwargs)
        return self

    def get_lookup(self, name):
        return self.output_field.get_lookup(name)

    def get_transform(self, name):
        """
        Validate that `name` is either a field of an embedded model or a
        lookup on an embedded model's field.
        """
        # Once the sub lhs is a transform, all the filter are applied over it.
        # Otherwise get transform from EMF.
        if transform := self._lhs.get_transform(name):
            if isinstance(transform, KeyTransformFactory):
                raise ValueError("Cannot perform multiple levels of array traversal in a query.")
            self._sub_transform = transform
            return self
        output_field = self._lhs.output_field
        allowed_lookups = self.array_field.ALLOWED_LOOKUPS.intersection(
            set(output_field.get_lookups())
        )
        suggested_lookups = difflib.get_close_matches(name, allowed_lookups)
        if suggested_lookups:
            suggested_lookups = " or ".join(suggested_lookups)
            suggestion = f", perhaps you meant {suggested_lookups}?"
        else:
            suggestion = ""
        raise FieldDoesNotExist(
            f"Unsupported lookup '{name}' for "
            f"EmbeddedModelArrayField of '{output_field.__class__.__name__}'"
            f"{suggestion}"
        )

    def as_mql(self, compiler, connection):
        inner_lhs_mql = self._lhs.as_mql(compiler, connection)
        lhs_mql = process_lhs(self, compiler, connection)
        return lhs_mql, inner_lhs_mql

    @property
    def output_field(self):
        return self.array_field


class KeyTransformFactory:
    def __init__(self, key_name, base_field):
        self.key_name = key_name
        self.base_field = base_field

    def __call__(self, *args, **kwargs):
        return KeyTransform(self.key_name, self.base_field, *args, **kwargs)
