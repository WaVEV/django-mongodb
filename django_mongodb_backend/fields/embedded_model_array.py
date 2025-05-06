import difflib

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import Field
from django.db.models.expressions import Col
from django.db.models.lookups import Transform

from .. import forms
from ..query_utils import process_lhs, process_rhs
from . import EmbeddedModelField
from .array import ArrayField
from .embedded_model import EMFExact


class EmbeddedModelArrayField(ArrayField):
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
        field = self.base_field.embedded_model._meta.get_field(name)
        return KeyTransformFactory(name, field)


@EmbeddedModelArrayField.register_lookup
class EMFArrayExact(EMFExact):
    def as_mql(self, compiler, connection):
        lhs_mql = process_lhs(self, compiler, connection)
        value = process_rhs(self, compiler, connection)
        if isinstance(self.lhs, Col | KeyTransform):
            if isinstance(value, models.Model):
                value, emf_data = self.model_to_dict(value)
                # Get conditions for any nested EmbeddedModelFields.
                conditions = self.get_conditions({"$$item": (value, emf_data)})
                return {
                    "$anyElementTrue": {
                        "$map": {"input": lhs_mql, "as": "item", "in": {"$and": conditions}}
                    }
                }
            lhs_mql = process_lhs(self.lhs, compiler, connection)
            return {
                "$anyElementTrue": {
                    "$map": {
                        "input": lhs_mql,
                        "as": "item",
                        "in": {"$eq": [f"$$item.{self.lhs.key_name}", value]},
                    }
                }
            }
        return connection.mongo_operators[self.lookup_name](lhs_mql, value)


class KeyTransform(Transform):
    # it should be different class than EMF keytransform even most of the methods are equal.
    def __init__(self, key_name, ref_field, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key_name = str(key_name)
        self.ref_field = ref_field

    def get_lookup(self, name):
        return self.output_field.get_lookup(name)

    def get_transform(self, name):
        """
        Validate that `name` is either a field of an embedded model or a
        lookup on an embedded model's field.
        """
        if transform := self.ref_field.get_transform(name):
            return transform
        suggested_lookups = difflib.get_close_matches(name, self.ref_field.get_lookups())
        if suggested_lookups:
            suggested_lookups = " or ".join(suggested_lookups)
            suggestion = f", perhaps you meant {suggested_lookups}?"
        else:
            suggestion = "."
        raise FieldDoesNotExist(
            f"Unsupported lookup '{name}' for "
            f"{self.ref_field.__class__.__name__} '{self.ref_field.name}'"
            f"{suggestion}"
        )

    def as_mql(self, compiler, connection):
        lhs_mql = process_lhs(self, compiler, connection)
        return f"{lhs_mql}.{self.key_name}"

    @property
    def output_field(self):
        return EmbeddedModelArrayField(self.ref_field)


class KeyTransformFactory:
    def __init__(self, key_name, ref_field):
        self.key_name = key_name
        self.ref_field = ref_field

    def __call__(self, *args, **kwargs):
        return KeyTransform(self.key_name, self.ref_field, *args, **kwargs)
