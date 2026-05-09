import os
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import manage
from models.user import UserRole


class _AsyncSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ResultStub:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class ManageRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_manage_import_fallback_uses_simple_normalizers(self):
        real_import = __import__

        def failing_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'core.utils':
                raise ImportError('missing utils')
            return real_import(name, globals, locals, fromlist, level)

        with patch('builtins.__import__', side_effect=failing_import):
            module_globals = runpy.run_module('manage', run_name='manage_import_fallback')

        self.assertEqual(module_globals['normalize_account_name'](' Demo '), 'demo')
        self.assertEqual(module_globals['normalize_persian_numerals'](' ۱۲۳ '), '۱۲۳')

    def test_run_migrations_requires_env_var(self):
        with patch.dict(os.environ, {}, clear=True), patch('manage.sys.exit', side_effect=SystemExit) as exit_mock:
            with self.assertRaises(SystemExit):
                manage.run_migrations()

        exit_mock.assert_called_once_with(1)

    def test_run_migrations_configures_alembic_and_handles_failure(self):
        fake_cfg = MagicMock()
        with patch.dict(os.environ, {'SYNC_DATABASE_URL': 'postgresql://db'}), patch(
            'manage.Config', return_value=fake_cfg
        ) as config_ctor, patch('manage.command.upgrade') as upgrade:
            manage.run_migrations()

        config_ctor.assert_called_once_with('alembic.ini')
        fake_cfg.set_main_option.assert_called_once_with('sqlalchemy.url', 'postgresql://db')
        upgrade.assert_called_once_with(fake_cfg, 'head')

        with patch.dict(os.environ, {'SYNC_DATABASE_URL': 'postgresql://db'}), patch(
            'manage.Config', return_value=fake_cfg
        ), patch('manage.command.upgrade', side_effect=RuntimeError('boom')), patch(
            'manage.sys.exit', side_effect=SystemExit
        ) as exit_mock:
            with self.assertRaises(SystemExit):
                manage.run_migrations()
        exit_mock.assert_called_once_with(1)

    async def test_create_super_admin_async_validates_input_and_creates_user(self):
        with patch('manage.input', side_effect=['', 'unused']):
            await manage.create_super_admin_async()

        with patch('manage.input', side_effect=['Demo', '', 'unused']):
            await manage.create_super_admin_async()

        with patch('manage.input', side_effect=['Demo', 'demo', '']):
            await manage.create_super_admin_async()

        with patch('manage.input', side_effect=['Demo', 'demo', '0912', 'not-a-number']):
            await manage.create_super_admin_async()

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[_ResultStub(None), _ResultStub(None), _ResultStub(None)])
        session.add = MagicMock()
        session.commit = AsyncMock()

        with patch('manage.input', side_effect=['Demo User', 'Demo', '۰۹۱۲۰۰۰۰۰۰۰', '1234']), patch.object(
            manage, 'AsyncSessionLocal', return_value=_AsyncSessionContext(session)
        ):
            await manage.create_super_admin_async()

        created_user = session.add.call_args.args[0]
        self.assertEqual(created_user.full_name, 'Demo User')
        self.assertEqual(created_user.account_name, 'demo')
        self.assertEqual(created_user.mobile_number, '09120000000')
        self.assertEqual(created_user.telegram_id, 1234)
        self.assertEqual(created_user.role, UserRole.SUPER_ADMIN)
        session.commit.assert_awaited_once()

    async def test_create_super_admin_async_rejects_duplicate_records(self):
        duplicate_account_session = AsyncMock()
        duplicate_account_session.execute = AsyncMock(return_value=_ResultStub(object()))

        with patch('manage.input', side_effect=['Demo', 'demo', '09120000000', '1234']), patch.object(
            manage, 'AsyncSessionLocal', return_value=_AsyncSessionContext(duplicate_account_session)
        ):
            await manage.create_super_admin_async()

        duplicate_mobile_session = AsyncMock()
        duplicate_mobile_session.execute = AsyncMock(side_effect=[_ResultStub(None), _ResultStub(object())])
        with patch('manage.input', side_effect=['Demo', 'demo', '09120000000', '1234']), patch.object(
            manage, 'AsyncSessionLocal', return_value=_AsyncSessionContext(duplicate_mobile_session)
        ):
            await manage.create_super_admin_async()

        duplicate_tg_session = AsyncMock()
        duplicate_tg_session.execute = AsyncMock(
            side_effect=[_ResultStub(None), _ResultStub(None), _ResultStub(object())]
        )
        with patch('manage.input', side_effect=['Demo', 'demo', '09120000000', '1234']), patch.object(
            manage, 'AsyncSessionLocal', return_value=_AsyncSessionContext(duplicate_tg_session)
        ):
            await manage.create_super_admin_async()

    def test_main_routes_commands(self):
        fake_coroutine = object()
        with patch.object(manage.sys, 'argv', ['manage.py', 'create_super_admin']), patch(
            'manage.asyncio.run'
        ) as async_run, patch('manage.create_super_admin_async', new=MagicMock(return_value=fake_coroutine)) as create_super_admin:
            manage.main()
        create_super_admin.assert_called_once_with()
        async_run.assert_called_once_with(fake_coroutine)

        with patch.object(manage.sys, 'argv', ['manage.py']), patch('manage.run_migrations') as run_migrations:
            manage.main()
        run_migrations.assert_called_once()

    def test_main_handles_create_super_admin_interrupt_and_failure(self):
        fake_coroutine = object()
        with patch.object(manage.sys, 'argv', ['manage.py', 'create_super_admin']), patch(
            'manage.create_super_admin_async', new=MagicMock(return_value=fake_coroutine)
        ), patch('manage.asyncio.run', side_effect=KeyboardInterrupt), patch('builtins.print') as print_mock:
            manage.main()

        print_mock.assert_any_call('\nOperation cancelled.')

        with patch.object(manage.sys, 'argv', ['manage.py', 'create_super_admin']), patch(
            'manage.create_super_admin_async', new=MagicMock(return_value=fake_coroutine)
        ), patch('manage.asyncio.run', side_effect=RuntimeError('boom')), patch('builtins.print') as print_mock:
            manage.main()

        print_mock.assert_any_call('!!! Error creating super admin: boom')

    def test_manage_module_main_entrypoint_runs_main(self):
        fake_cfg = MagicMock()
        with patch.dict(os.environ, {'SYNC_DATABASE_URL': 'postgresql://db'}), patch.object(
            manage.sys, 'argv', ['manage.py']
        ), patch('alembic.config.Config', return_value=fake_cfg), patch('alembic.command.upgrade') as upgrade:
            runpy.run_module('manage', run_name='__main__')

        upgrade.assert_called_once()


if __name__ == '__main__':
    unittest.main()