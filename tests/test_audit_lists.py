"""Every table of the models is either tracked by the audit log or listed with the reason it is not (app/audit.py): adding a table forces a decision."""
from app import audit, models  # noqa: F401 — registers the tables
from app.db import Base


def test_every_table_is_tracked_or_listed_with_a_reason():
    tables = set(Base.metadata.tables)
    listed = set(audit.TRACKED) | set(audit.NOT_TRACKED)
    assert tables == listed, (tables - listed, listed - tables)
    assert not set(audit.TRACKED) & set(audit.NOT_TRACKED)
    assert all(audit.NOT_TRACKED.values())


def test_no_address_name_or_hash_of_an_account_is_logged():
    assert {'email', 'name', 'password_hash'} <= set(audit.TRACKED['admin_users'])


def test_ignored_columns_exist_on_their_tables():
    for table, ignored in audit.TRACKED.items():
        columns = set(Base.metadata.tables[table].columns.keys())
        assert set(ignored) <= columns, (table, set(ignored) - columns)


def test_a_table_without_a_primary_key_is_never_tracked():
    for table in audit.TRACKED:
        assert Base.metadata.tables[table].primary_key.columns, table
