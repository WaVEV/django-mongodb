from decimal import Decimal

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.test import SimpleTestCase, TestCase

from django_mongodb.fields import EmbeddedModelField

from .models import (
    Address,
    Author,
    Book,
    DecimalKey,
    EmbeddedModel,
    EmbeddedModelFieldModel,
)


class MethodTests(SimpleTestCase):
    def test_deconstruct(self):
        field = EmbeddedModelField("EmbeddedModel", null=True)
        name, path, args, kwargs = field.deconstruct()
        self.assertEqual(path, "django_mongodb.fields.EmbeddedModelField")
        self.assertEqual(args, [])
        self.assertEqual(kwargs, {"embedded_model": "EmbeddedModel", "null": True})

    def test_get_db_prep_save_invalid(self):
        msg = (
            "Expected instance of type <class 'model_fields_.models.EmbeddedModel'>, "
            "not <class 'int'>."
        )
        with self.assertRaisesMessage(TypeError, msg):
            EmbeddedModelFieldModel(simple=42).save()

    def test_validate(self):
        obj = EmbeddedModelFieldModel(simple=EmbeddedModel(someint=None))
        # This isn't quite right because "someint" is the field that's non-null.
        msg = "{'simple': ['This field cannot be null.']}"
        with self.assertRaisesMessage(ValidationError, msg):
            obj.full_clean()


class ModelTests(TestCase):
    def truncate_ms(self, value):
        """Truncate microsends to millisecond precision as supported by MongoDB."""
        return value.replace(microsecond=(value.microsecond // 1000) * 1000)

    def test_save_load(self):
        EmbeddedModelFieldModel.objects.create(simple=EmbeddedModel(someint="5"))
        obj = EmbeddedModelFieldModel.objects.get()
        self.assertIsInstance(obj.simple, EmbeddedModel)
        # Make sure get_prep_value is called.
        self.assertEqual(obj.simple.someint, 5)
        # Primary keys should not be populated...
        self.assertEqual(obj.simple.id, None)
        # ... unless set explicitly.
        obj.simple.id = obj.id
        obj.save()
        obj = EmbeddedModelFieldModel.objects.get()
        self.assertEqual(obj.simple.id, obj.id)

    def test_save_load_null(self):
        EmbeddedModelFieldModel.objects.create(simple=None)
        obj = EmbeddedModelFieldModel.objects.get()
        self.assertIsNone(obj.simple)

    def test_pre_save(self):
        """Field.pre_save() is called on embedded model fields."""
        obj = EmbeddedModelFieldModel.objects.create(simple=EmbeddedModel())
        auto_now = self.truncate_ms(obj.simple.auto_now)
        auto_now_add = self.truncate_ms(obj.simple.auto_now_add)
        self.assertEqual(auto_now, auto_now_add)
        # save() updates auto_now but not auto_now_add.
        obj.save()
        self.assertEqual(self.truncate_ms(obj.simple.auto_now_add), auto_now_add)
        auto_now_two = obj.simple.auto_now
        self.assertGreater(auto_now_two, obj.simple.auto_now_add)
        # And again, save() updates auto_now but not auto_now_add.
        obj = EmbeddedModelFieldModel.objects.get()
        obj.save()
        self.assertEqual(obj.simple.auto_now_add, auto_now_add)
        self.assertGreater(obj.simple.auto_now, auto_now_two)

    def test_embedded_field_with_foreign_conversion(self):
        decimal = DecimalKey.objects.create(decimal=Decimal("1.5"))
        # decimal_parent = DecimalParent.objects.create(child=decimal)
        EmbeddedModelFieldModel.objects.create(decimal_parent=decimal)


class QueryingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.objs = [
            EmbeddedModelFieldModel.objects.create(simple=EmbeddedModel(someint=x))
            for x in range(6)
        ]

    def test_exact(self):
        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__someint=3), [self.objs[3]]
        )

    def test_lt(self):
        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__someint__lt=3), self.objs[:3]
        )

    def test_lte(self):
        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__someint__lte=3), self.objs[:4]
        )

    def test_gt(self):
        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__someint__gt=3), self.objs[4:]
        )

    def test_gte(self):
        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__someint__gte=3), self.objs[3:]
        )

    def test_nested(self):
        obj = Book.objects.create(
            author=Author(name="Shakespeare", age=55, address=Address(city="NYC", state="NY"))
        )
        self.assertCountEqual(Book.objects.filter(author__address__city="NYC"), [obj])

    def test_nested_not_exists(self):
        msg = "Address has no field named 'president'"
        with self.assertRaisesMessage(FieldDoesNotExist, msg):
            Book.objects.filter(author__address__city__president="NYC")

    def test_not_exists_in_embedded(self):
        msg = "Address has no field named 'floor'"
        with self.assertRaisesMessage(FieldDoesNotExist, msg):
            Book.objects.filter(author__address__floor="NYC")

    def test_embedded_with_json_field(self):
        models = []
        for i in range(4):
            m = EmbeddedModelFieldModel.objects.create(
                simple=EmbeddedModel(
                    json_value={"field1": i * 5, "field2": {"0": {"value": list(range(i))}}}
                )
            )
            models.append(m)

        all_models = EmbeddedModelFieldModel.objects.all()

        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__json_value__field2__0__value__0=0),
            models[1:],
        )
        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__json_value__field2__0__value__1=1),
            models[2:],
        )
        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__json_value__field2__0__value__1=5), []
        )

        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__json_value__field1__lt=100), all_models
        )
        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(simple__json_value__field1__gt=100), []
        )
        self.assertCountEqual(
            EmbeddedModelFieldModel.objects.filter(
                simple__json_value__field1__gte=5, simple__json_value__field1__lte=10
            ),
            models[1:3],
        )
