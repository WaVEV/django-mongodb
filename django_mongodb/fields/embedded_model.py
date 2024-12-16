from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models.fields.related import lazy_related_operation
from django.db.models.lookups import Transform

from .. import forms


class EmbeddedModelField(models.Field):
    """Field that stores a model instance."""

    def __init__(self, embedded_model, *args, **kwargs):
        """
        `embedded_model` is the model class of the instance that will be
        stored. Like other relational fields, it may also be passed as a
        string.
        """
        self.embedded_model = embedded_model
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if path.startswith("django_mongodb.fields.embedded_model"):
            path = path.replace("django_mongodb.fields.embedded_model", "django_mongodb.fields")
        kwargs["embedded_model"] = self.embedded_model
        return name, path, args, kwargs

    def get_internal_type(self):
        return "EmbeddedModelField"

    def _set_model(self, model):
        """
        Resolve embedded model class once the field knows the model it belongs
        to.

        If the model argument passed to __init__() was a string, resolve that
        string to the corresponding model class, similar to relation fields.
        However, we need to know our own model to generate a valid key
        for the embedded model class lookup and EmbeddedModelFields are
        not contributed_to_class if used in iterable fields. Thus the
        collection field sets this field's "model" attribute in its
        contribute_to_class().
        """
        self._model = model
        if model is not None and isinstance(self.embedded_model, str):

            def _resolve_lookup(_, resolved_model):
                self.embedded_model = resolved_model

            lazy_related_operation(_resolve_lookup, model, self.embedded_model)

    model = property(lambda self: self._model, _set_model)

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        """
        Passes embedded model fields' values through embedded fields
        to_python() and reinstiatates the embedded instance.
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            return value
        # Create the model instance.
        instance = self.embedded_model(
            **{
                # Pass values through respective fields' to_python(), leaving
                # fields for which no value is specified uninitialized.
                field.attname: field.to_python(value[field.attname])
                for field in self.embedded_model._meta.fields
                if field.attname in value
            }
        )
        instance._state.adding = False
        return instance

    def get_db_prep_save(self, embedded_instance, connection):
        """
        Apply pre_save() and get_db_prep_save() of embedded instance
        fields and passes a field => value mapping down to database
        type conversions.

        The embedded instance will be saved as a column => value dict, but
        because we need to apply database type conversions on embedded instance
        fields' values and for these we need to know fields those values come
        from, we need to entrust the database layer with creating the dict.
        """
        if embedded_instance is None:
            return None
        if not isinstance(embedded_instance, self.embedded_model):
            raise TypeError(
                f"Expected instance of type {self.embedded_model!r}, not "
                f"{type(embedded_instance)!r}."
            )
        # Apply pre_save() and get_db_prep_save() of embedded instance
        # fields, create the field => value mapping to be passed to
        # storage preprocessing.
        field_values = {}
        add = embedded_instance._state.adding
        for field in embedded_instance._meta.fields:
            value = field.get_db_prep_save(
                field.pre_save(embedded_instance, add), connection=connection
            )
            # Exclude unset primary keys (e.g. {'id': None}).
            if field.primary_key and value is None:
                continue
            field_values[field.attname] = value
        # This instance will exist in the database soon.
        # TODO.XXX: Ensure that this doesn't cause race conditions.
        embedded_instance._state.adding = False
        return field_values

    def get_transform(self, name):
        transform = super().get_transform(name)
        if transform:
            return transform
        field = self.embedded_model._meta.get_field(name)
        return KeyTransformFactory(name, field)

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        if self.embedded_model is None:
            return
        for field in self.embedded_model._meta.fields:
            attname = field.attname
            field.validate(getattr(value, attname), model_instance)

    def formfield(self, **kwargs):
        return super().formfield(
            **{
                "form_class": forms.EmbeddedModelFormField,
                "model": self.embedded_model,
                "name": self.name,
                **kwargs,
            }
        )


class KeyTransform(Transform):
    def __init__(self, key_name, ref_field=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key_name = str(key_name)
        self.ref_field = ref_field

    def get_transform(self, name):
        result = None
        if isinstance(self.ref_field, EmbeddedModelField):
            opts = self.ref_field.embedded_model._meta
            new_field = opts.get_field(name)
            result = KeyTransformFactory(name, new_field)
        else:
            if self.ref_field.get_transform(name) is None:
                raise FieldDoesNotExist(
                    f"{self.ref_field.model._meta.object_name} has no field named '{name}'"
                )
            result = KeyTransformFactory(name, self.ref_field)
        return result

    def preprocess_lhs(self, compiler, connection):
        previous = self
        embedded_key_transforms = []
        json_key_transforms = []
        while isinstance(previous, KeyTransform):
            if isinstance(previous.ref_field, EmbeddedModelField):
                embedded_key_transforms.insert(0, previous.key_name)
            else:
                json_key_transforms.insert(0, previous.key_name)
            previous = previous.lhs
        mql = previous.as_mql(compiler, connection)
        embedded_key_transforms.append(json_key_transforms.pop(0))
        return mql, embedded_key_transforms, json_key_transforms


def key_transform(self, compiler, connection):
    mql, key_transforms, json_key_transforms = self.preprocess_lhs(compiler, connection)
    transforms = ".".join(key_transforms)
    result = f"{mql}.{transforms}"
    for key in json_key_transforms:
        get_field = {"$getField": {"input": result, "field": key}}
        # Handle array indexing if the key is a digit. If key is something
        # like '001', it's not an array index despite isdigit() returning True.
        if key.isdigit() and str(int(key)) == key:
            result = {
                "$cond": {
                    "if": {"$isArray": result},
                    "then": {"$arrayElemAt": [result, int(key)]},
                    "else": get_field,
                }
            }
        else:
            result = get_field
    return result


class KeyTransformFactory:
    def __init__(self, key_name, ref_field=None):
        self.key_name = key_name
        self.ref_field = ref_field

    def __call__(self, *args, **kwargs):
        return KeyTransform(self.key_name, self.ref_field, *args, **kwargs)


def register_embedded_model_field():
    KeyTransform.as_mql = key_transform
