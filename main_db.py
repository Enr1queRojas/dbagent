"""Database smoke test. Run locally on the Windows machine hosting SQLEXPRESS."""
import logging
from core.database_client import DatabaseClient, DatabaseError


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        db = DatabaseClient.from_yaml()
        print("CONEXIÓN")
        print(db.test_connection().to_json())
        print("TABLAS Y VISTAS")
        print(db.list_tables().to_json())
        print("COLUMNAS DE PRODUCTS")
        print(db.get_columns("Products").to_json())
        print("PRODUCTOS (ejemplo Northwind)")
        result = db.query(
            "SELECT ProductID, ProductName, UnitPrice FROM dbo.Products WHERE UnitPrice >= ? ORDER BY UnitPrice DESC, ProductID",
            params=(20,), max_rows=10,
        )
        print(result.to_json())
        return 0
    except (DatabaseError, ValueError, OSError) as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
