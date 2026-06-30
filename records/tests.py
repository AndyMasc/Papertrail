from django.test import TestCase
from records.models import Record
from django.contrib.auth.models import User
from django.urls import reverse


class RecordTestCase(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="testuser", password="password123")
        self.user2 = User.objects.create_user(username="testuser2", password="password123")
        
        self.record1 = Record.objects.create(
            user=self.user1,
            title="Test Record", 
            merchant="Test Merchant", 
            balance=100.00, 
            transaction_date="2023-01-01", 
            expiry_date=None,
            record_type="expense_receipt") 

        self.record2 = Record.objects.create(
            user=self.user2,
            title="Test Record 2", 
            merchant="Test Merchant 2", 
            balance=200.00, 
            transaction_date="2023-01-02", 
            expiry_date=None,
            record_type="expense_receipt")

    def test_record_creation(self):
        self.assertEqual(self.record1.title, "Test Record")
        self.assertEqual(self.record1.balance, 100.00)
        self.assertEqual(self.record1.transaction_date, "2023-01-01")
        self.assertEqual(self.record1.record_type, "expense_receipt")
        self.assertEqual(self.record1.merchant, "Test Merchant")
        self.assertEqual(self.record1.expiry_date, None)
        
        self.assertEqual(self.record2.title, "Test Record 2")
        self.assertEqual(self.record2.balance, 200.00)
        self.assertEqual(self.record2.transaction_date, "2023-01-02")
        self.assertEqual(self.record2.record_type, "expense_receipt")
        self.assertEqual(self.record2.merchant, "Test Merchant 2")
        self.assertEqual(self.record2.expiry_date, None)
        

    def test_record_access(self):
        user1 = User.objects.create_user(username="u1", password="pass")
        user2 = User.objects.create_user(username="u2", password="pass")
    
        Record.objects.create(user=user1, title="A")
        Record.objects.create(user=user2, title="B")
    
        self.client.force_login(user1)
        response = self.client.get(reverse("records:view_all_records"))
    
        records = response.context["records"]
    
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "A")
        
        titles = [record.title for record in records]
        self.assertNotIn("B", titles)