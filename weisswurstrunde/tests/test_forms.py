from django.test import TestCase, override_settings

from weisswurstrunde.forms import ManualPaymentForm, RegisterForm


@override_settings(INVITATION_CODE="test-invitation")
class FormTests(TestCase):
    def test_registration_validation(self):
        data = {
            "name": "Stefan",
            "email": "STEFAN@example.org",
            "paypal_email": "different@example.org",
            "password": "a-very-long-random-test-pass",
            "password_confirm": "a-very-long-random-test-pass",
            "invitation": "test-invitation",
        }
        form = RegisterForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.email, "stefan@example.org")
        self.assertEqual(user.paypal_email, "different@example.org")
        self.assertTrue(user.check_password(data["password"]))
        for invitation in ["wrong", "wrong-\u2603"]:
            data["invitation"] = invitation
            self.assertFalse(RegisterForm(data).is_valid())

    def test_registration_accepts_passwords_without_strength_restrictions(self):
        for password in ["a", "123456789", "password", "stefan@example.org"]:
            with self.subTest(password=password):
                form = RegisterForm(
                    {
                        "name": "Stefan",
                        "email": "stefan@example.org",
                        "paypal_email": "different@example.org",
                        "password": password,
                        "password_confirm": password,
                        "invitation": "test-invitation",
                    }
                )
                self.assertTrue(form.is_valid(), form.errors)
                user = form.save(commit=False)
                self.assertNotEqual(user.password, password)
                self.assertTrue(user.check_password(password))

    def test_registration_still_requires_matching_nonempty_passwords(self):
        for password, confirmation in [("", ""), ("a", "b")]:
            with self.subTest(password=password, confirmation=confirmation):
                form = RegisterForm(
                    {
                        "name": "Stefan",
                        "email": "stefan@example.org",
                        "paypal_email": "different@example.org",
                        "password": password,
                        "password_confirm": confirmation,
                        "invitation": "test-invitation",
                    }
                )
                self.assertFalse(form.is_valid())
                self.assertIn("password_confirm", form.errors)

    def test_payment_precision_and_positive_amount(self):
        import uuid

        data = {"amount": "13.60", "method": "CASH", "request_id": str(uuid.uuid4())}
        form = ManualPaymentForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cents, 1360)
        for amount in ["0", "-1", "1.001", "NaN", "Infinity", "10001"]:
            data["amount"] = amount
            self.assertFalse(ManualPaymentForm(data).is_valid())
