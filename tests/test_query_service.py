import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock

from core.database_client import QueryResult
from core.query_service import QueryService


class QueryServiceTests(unittest.TestCase):
    def test_rejects_and_preserves_values(self):
        db = Mock()
        service = QueryService(db)
        with self.assertRaises(PermissionError):
            service.execute({"allowed": False, "sql": "SELECT 1"})
        db.query.assert_not_called()
        params = [Decimal("1.20"), date(2024, 1, 2), None]
        db.query.return_value = QueryResult(["amount", "day"], [(params[0], params[1])], True, 0.25)
        result = service.execute({"allowed": True, "sql": "SELECT ?, ?", "params": params})
        db.query.assert_called_once_with("SELECT ?, ?", params)
        self.assertEqual(result.rows[0], (Decimal("1.20"), date(2024, 1, 2)))
        self.assertTrue(result.truncated)
        self.assertEqual(result.elapsed_seconds, .25)


if __name__ == "__main__": unittest.main()
