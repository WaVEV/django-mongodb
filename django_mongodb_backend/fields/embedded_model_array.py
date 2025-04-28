from ..forms import EmbeddedModelArrayFormField
from ..query_utils import process_rhs
from . import EmbeddedModelField
from .array import ArrayField
from .embedded_model import EMFExact, KeyTransformFactory


class EmbeddedModelArrayField(ArrayField):
    def __init__(self, model, **kwargs):
        super().__init__(EmbeddedModelField(model), **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if path == "django_mongodb_backend.fields.multiple_embedded_model.EmbeddedModelArrayField":
            path = "django_mongodb_backend.fields.EmbeddedModelArrayField"
        kwargs.update(
            {
                "model": self.base_field.embedded_model,
                "size": self.size,
            }
        )
        del kwargs["base_field"]
        return name, path, args, kwargs

    def get_db_prep_value(self, value, connection, prepared=False):
        if isinstance(value, list | tuple):
            return [self.base_field.get_db_prep_save(i, connection) for i in value]
        return value

    def formfield(self, **kwargs):
        return super().formfield(
            **{
                "form_class": EmbeddedModelArrayFormField,
                "model": self.base_field.embedded_model,
                "max_length": self.size,
                "prefix": self.name,
                **kwargs,
            }
        )

    def get_transform(self, name):
        # return self.base_field.get_transform(name)
        # Copied from EmbedddedModelField -- customize?
        if transform := super().get_transform(name):
            return transform
        if name.isdigit():
            return KeyTransformFactory(name, self)
        field = self.embedded_model._meta.get_field(name)
        return KeyTransformFactory(name, field)


@EmbeddedModelArrayField.register_lookup
class EMFArrayExact(EMFExact):
    def as_mql(self, compiler, connection):
        mql, key_transforms, json_key_transforms = self.lhs.preprocess_lhs(compiler, connection)
        transforms = ".".join(key_transforms)
        value = process_rhs(self, compiler, connection)
        # return {"$anyElementTrue": []}
        # transforms = build_json_mql_path("$$this", key_transforms)
        return {
            "$reduce": {
                "input": mql,
                "initialValue": False,
                "in": {"$or": ["$$value", {"$eq": [f"$$this.{transforms}", value]}]},
            }
        }
        # return super().as_mql(compiler, connection)
