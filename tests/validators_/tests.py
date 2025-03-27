from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from django_mongodb_backend.validators import LengthValidator


class TestValidators(SimpleTestCase):
    def test_validators(self):
        validator = LengthValidator(10)
        with self.assertRaises(ValidationError) as context_manager:
            validator([])
        self.assertEqual(
            context_manager.exception.messages, ["List contains 0 items, it should contain 10."]
        )
        with self.assertRaises(ValidationError) as context_manager:
            validator([1])
        self.assertEqual(
            context_manager.exception.messages, ["List contains 1 item, it should contain 10."]
        )
        with self.assertRaises(ValidationError) as context_manager:
            validator(list(range(11)))
        self.assertEqual(
            context_manager.exception.messages, ["List contains 11 items, it should contain 10."]
        )
        self.assertEqual(validator(list(range(10))), None)
