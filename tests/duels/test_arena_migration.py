"""Migration 0157 only creates arena tables and touches nothing that exists."""
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, override_settings


@override_settings(MIGRATION_MODULES={})
class MigrationTests(TestCase):
    def test_only_arena_operations(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        migration = loader.get_migration('judge', '0157_arena')
        self.assertEqual(migration.dependencies, [('judge', '0156_problemdata_interactor')])
        for operation in migration.operations:
            name = getattr(operation, 'model_name', None) or getattr(operation, 'name', '')
            self.assertTrue(name.lower().startswith('arena'), operation.describe())
            self.assertIn(type(operation).__name__, ('CreateModel', 'AddField', 'AddIndex', 'AlterUniqueTogether'))
