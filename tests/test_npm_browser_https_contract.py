"""Public producer/consumer contract checks, without configured App imports."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from starlette.responses import Response

ROOT=Path(__file__).absolute().parents[1]

def actual_functions(relative,names,namespace):
    raw=(ROOT/relative).read_bytes()
    if len(raw)>1048576:raise AssertionError('public_source_bound')
    tree=ast.parse(raw)
    selected=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))and n.name in names]
    if {n.name for n in selected}!=set(names):raise AssertionError('public_function_inventory')
    future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
    module=ast.Module(body=[future]+selected,type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module,str(ROOT/relative),'exec'),namespace)
    return namespace

class BrowserHttpsContractTests(unittest.TestCase):
    def test_actual_staging_cookie_producer_keeps_Host_Secure_HttpOnly_Strict(self):
        ns=actual_functions('api/routers/auth.py',{
            '_registration_context_secure_cookie','_registration_context_cookie_name',
            '_set_registration_context_cookie',
        },{'settings':SimpleNamespace(environment='staging'),'Response':Response,
           'REGISTRATION_CONTEXT_COOKIE_PRODUCTION':'__Host-web_registration',
           'REGISTRATION_CONTEXT_COOKIE_DEVELOPMENT':'web_registration',
           'REGISTRATION_CONTEXT_TTL_SECONDS':600})
        response=Response()
        ns['_set_registration_context_cookie'](response,'OWNED_PUBLIC_CONTEXT')
        cookie=response.headers['set-cookie']
        self.assertTrue(cookie.startswith('__Host-web_registration='))
        self.assertIn('Secure',cookie);self.assertIn('HttpOnly',cookie)
        self.assertIn('SameSite=strict',cookie);self.assertIn('Path=/',cookie)
        self.assertNotIn('Domain=',cookie)

    def test_actual_context_reconciliation_explains_why_challenge_must_follow_initial_load(self):
        class State(SimpleNamespace):
            def model_copy(self,*,update):return State(**{**vars(self),**update})
        class Redis:
            def __init__(self,present):self.present=present
            async def get(self,key):return '12345'if self.present and key=='reg_otp:OWNED_PUBLIC_INVITATION'else None
        async def load(_db,_redis,_handle):
            return State(kind='invitation',progress='context_ready',invitation_token='OWNED_PUBLIC_INVITATION'),object()
        async def update(*_args):return None
        class Completed(Exception):pass
        ns=actual_functions('api/routers/auth.py',{'_load_registration_context'},{
            '_registration_context_handle':lambda _request:'OWNED_PUBLIC_CONTEXT',
            '_load_registration_context_by_handle':load,
            '_registration_context_verified_key':lambda _handle:'OWNED_PUBLIC_VERIFIED_KEY',
            '_update_registration_context':update,'RegistrationContextAlreadyCompleted':Completed,
        })
        for present,expected in((False,'context_ready'),(True,'otp_requested')):
            with self.subTest(challenge_already_present=present):
                result=asyncio.run(ns['_load_registration_context'](object(),Redis(present),object()))
                self.assertEqual(result[1].progress,expected)

    def test_both_real_fixture_challenges_follow_context_metadata_and_precede_request(self):
        customer=(ROOT/'frontend/e2e/customer-owner-flow.spec.ts').read_text()
        mandatory=(ROOT/'frontend/e2e/mandatory-channel.spec.ts').read_text()
        body=customer[customer.index("await page.goto(toRelativeRegistrationPath(registrationLink))"):]
        self.assertLess(body.index('getByText(customerAccountName)'),body.index('seedRegistrationOtpDelivery('))
        self.assertLess(body.index('seedRegistrationOtpDelivery('),body.index("name: 'دریافت کد تأیید'"))
        body=mandatory[mandatory.index('await page.goto(invitation.shortLinkPath)'):]
        self.assertLess(body.index('getByText(invitation.accountName)'),body.index('`reg_otp:${invitation.token}`'))
        self.assertLess(body.index('`reg_otp:${invitation.token}`'),body.index("name: 'دریافت کد تأیید'"))
        for text in(customer,mandatory):
            self.assertNotIn('reg_verified:',text)
            self.assertNotIn('registration_context_verified:',text)
        self.assertIn('/api/auth/registration-context/otp/verify',mandatory)

    def test_actual_public_contact_serializer_never_exposes_relation_or_management_metadata(self):
        user=SimpleNamespace(id=77,account_name='OWNED_PUBLIC_USER',avatar_file_id=None,
            mobile_number='09120000077',address='OWNED_PUBLIC_ADDRESS',
            accountant_relations=['OWNED_PRIVATE_RELATION'],customer_management_name='OWNED_PRIVATE_NAME',
            customer_relations=['OWNED_PRIVATE_CUSTOMER'],trades_count=99)
        ns=actual_functions('api/routers/users_public.py',{'_serialize_public_user'},
            {'schemas':SimpleNamespace(UserPublicRead=lambda **kwargs:kwargs)})
        result=ns['_serialize_public_user'](user)
        self.assertEqual(set(result),{'id','account_name','avatar_file_id','mobile_number','address'})
        self.assertEqual(result['mobile_number'],user.mobile_number)
        self.assertFalse(any('PRIVATE'in str(value)for value in result.values()))

    def test_actual_customer_visibility_has_no_super_admin_bypass(self):
        ns=actual_functions('api/routers/users_public.py',{'_can_view_customer_profile'}, {})
        relation=SimpleNamespace(customer_user_id=77,owner_user_id=88)
        allowed=ns['_can_view_customer_profile']
        for viewer,want in((77,True),(88,True),(99,False)):
            self.assertEqual(allowed(SimpleNamespace(id=viewer,role='super_admin'),relation,
                viewer_accountant_relation=None),want)
        self.assertTrue(allowed(SimpleNamespace(id=99),relation,
            viewer_accountant_relation=SimpleNamespace(owner_user_id=88)))
        self.assertFalse(allowed(SimpleNamespace(id=99),relation,
            viewer_accountant_relation=SimpleNamespace(owner_user_id=55)))

    def test_https_keeps_fixture_staging_and_all_twelve_browser_gates(self):
        import yaml
        value=yaml.load((ROOT/'.github/workflows/npm-security-backports.yml').read_text(),Loader=yaml.BaseLoader)
        job=value['jobs']['browsers'];self.assertEqual(job['strategy']['matrix'],
            {'browser':['chromium','firefox','webkit'],'shard':['1','2','3','4']})
        self.assertEqual(job['timeout-minutes'],'180')
        env_step=next(x for x in job['steps']if x.get('name')=='Disposable local-only CI environment (existing merge-gate pattern)')
        self.assertIn('ENVIRONMENT=staging',env_step['run'])
        self.assertIn('FRONTEND_URL=https://127.0.0.1:4173',env_step['run'])
        prepare=next(x for x in job['steps']if x.get('id')=='browser_tls')
        self.assertIn('mkdir -m 700 "$tls_dir"',prepare['run'])
        self.assertIn('>/dev/null 2>&1',prepare['run'])
        cleanup=next(x for x in job['steps']if x.get('name')=="Remove only this job's ephemeral browser TLS material")
        self.assertEqual(cleanup['if'],"always() && steps.browser_tls.outputs.created == 'true'")
        self.assertNotIn('rm -rf',cleanup['run'])
        self.assertIn('test ! -e "$tls_dir"',cleanup['run'])

if __name__=='__main__':unittest.main()
