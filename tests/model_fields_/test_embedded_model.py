import operator
from decimal import Decimal

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db.models import (
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    Max,
    Model,
    OuterRef,
    Subquery,
    Sum,
)
from django.test import SimpleTestCase, TestCase

from django_mongodb.fields import EmbeddedModelField

from .models import (
    Address,
    Author,
    Book,
    DecimalKey,
    DecimalParent,
    EmbeddedModel,
    EmbeddedModelFieldModel,
    Library,
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

    def test_foreign_key_in_embedded_object(self):
        msg = (
            "Field of type <class 'django.db.models.fields.related.ForeignKey'> "
            "is not supported within an EmbeddedModelField."
        )
        with self.assertRaisesMessage(TypeError, msg):

            class EmbeddedModelTest(Model):
                decimal = EmbeddedModelField(DecimalParent, null=True, blank=True)

    def test_embedded_field_with_foreign_conversion(self):
        decimal = DecimalKey.objects.create(decimal=Decimal("1.5"))
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

    def truncate_ms(self, value):
        """Truncate microsends to millisecond precision as supported by MongoDB."""
        return value.replace(microsecond=(value.microsecond // 1000) * 1000)

    ################
    def test_ordering_by_embedded_field(self):
        query = (
            EmbeddedModelFieldModel.objects.filter(simple__someint__gt=3)
            .order_by("-simple__someint")
            .values("pk")
        )
        expected = [{"pk": e.pk} for e in list(reversed(self.objs[4:]))]
        self.assertSequenceEqual(query, expected)

    def test_ordering_grouping_by_embedded_field(self):
        expected = sorted(
            (
                EmbeddedModelFieldModel.objects.create(simple=EmbeddedModel(someint=x))
                for x in range(6)
            ),
            key=lambda x: x.simple.someint,
        )
        query = (
            EmbeddedModelFieldModel.objects.annotate(
                group=ExpressionWrapper(F("simple__someint") + 5, output_field=IntegerField())
            )
            .values("group")
            .annotate(max_auto_now=Max("simple__auto_now"))
            .order_by("simple__someint")
        )
        query_response = [{**e, "max_auto_now": self.truncate_ms(e["max_auto_now"])} for e in query]
        self.assertSequenceEqual(
            query_response,
            [
                {"group": e.simple.someint + 5, "max_auto_now": self.truncate_ms(e.simple.auto_now)}
                for e in expected
            ],
        )

    def test_ordering_grouping_by_sum(self):
        [EmbeddedModelFieldModel.objects.create(simple=EmbeddedModel(someint=x)) for x in range(6)]
        qs = (
            EmbeddedModelFieldModel.objects.values("simple__someint")
            .annotate(sum=Sum("simple__someint"))
            .order_by("sum")
        )
        self.assertQuerySetEqual(qs, [0, 2, 4, 6, 8, 10], operator.itemgetter("sum"))


class SubqueryExistsTestCase(TestCase):
    def setUp(self):
        # Create test data
        address1 = Address.objects.create(city="New York", state="NY", zip_code=10001)
        address2 = Address.objects.create(city="Boston", state="MA", zip_code=20002)
        author1 = Author.objects.create(name="Alice", age=30, address=address1)
        author2 = Author.objects.create(name="Bob", age=40, address=address2)
        book1 = Book.objects.create(name="Book A", author=author1)
        book2 = Book.objects.create(name="Book B", author=author2)
        Book.objects.create(name="Book C", author=author2)
        Book.objects.create(name="Book D", author=author2)
        Book.objects.create(name="Book E", author=author1)

        library1 = Library.objects.create(
            name="Central Library", location="Downtown", best_seller="Book A"
        )
        library2 = Library.objects.create(
            name="Community Library", location="Suburbs", best_seller="Book A"
        )

        # Add books to libraries
        library1.books.add(book1, book2)
        library2.books.add(book2)

    def test_exists_subquery(self):
        subquery = Book.objects.filter(
            author__name=OuterRef("name"), author__address__city="Boston"
        )
        queryset = Author.objects.filter(Exists(subquery))

        self.assertEqual(queryset.count(), 1)

    def test_in_subquery(self):
        subquery = Author.objects.filter(age__gt=35).values("name")
        queryset = Book.objects.filter(author__name__in=Subquery(subquery)).order_by("name")

        self.assertEqual(queryset.count(), 3)
        self.assertQuerySetEqual(queryset, ["Book B", "Book C", "Book D"], lambda book: book.name)

    def test_range_query(self):
        queryset = Author.objects.filter(age__range=(25, 45)).order_by("name")

        self.assertEqual(queryset.count(), 2)
        self.assertQuerySetEqual(queryset, ["Alice", "Bob"], lambda author: author.name)

    def test_exists_with_foreign_object(self):
        subquery = Library.objects.filter(best_seller=OuterRef("name"))
        queryset = Book.objects.filter(Exists(subquery))

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().name, "Book A")

    def test_foreign_field_with_ranges(self):
        queryset = Library.objects.filter(books__author__age__range=(25, 35))

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().name, "Central Library")
