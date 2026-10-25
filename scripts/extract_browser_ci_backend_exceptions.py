"""Project bounded synthetic browser-CI stdin into closed traceback metadata."""
from collections import deque
import json
import re
import sys


MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_LINE_BYTES = 8192
MAX_OUTPUT_BYTES = 32768
MAX_REPORTS = 16
MAX_FRAMES = 8

# Exact public source names for the failing browser handler chains. Never echo
# an arbitrary log-supplied filename, class, function, message, or source line.
APPROVED_PATHS = frozenset("""
api/deps.py
api/routers/accountants.py
api/routers/customers.py
api/routers/invitations.py
api/routers/offers.py
bot/utils/redis_helpers.py
core/audit_logger.py
core/config.py
core/db.py
core/invitation_contract_service.py
core/offer_identity.py
core/offer_lifecycle.py
core/offer_priority_sync.py
core/public_webapp_url.py
core/redis.py
core/security.py
core/server_routing.py
core/sms.py
core/telegram_delivery_runtime_policy.py
core/trading_settings.py
core/utils.py
core/services/accountant_relation_service.py
core/services/canonical_invitation_creation_service.py
core/services/customer_relation_service.py
core/services/invitation_identity_reservation_service.py
core/services/invitation_lifecycle_service.py
core/services/invitation_requester_service.py
core/services/invitation_sms_delivery_service.py
core/services/market_schedule_service.py
core/services/market_transition_service.py
core/services/offer_creation_service.py
core/services/offer_model_price_guard.py
core/services/offer_publication_state_service.py
core/services/telegram_offer_publication_service.py
core/services/trade_service.py
models/accountant_relation.py
models/customer_relation.py
models/invitation.py
models/offer.py
models/session.py
models/user.py
""".split())
CLOSED_CLASSES = frozenset("""
AssertionError AttributeError IndexError KeyError NameError NotImplementedError
RuntimeError TypeError ValueError ZeroDivisionError OSError PermissionError
FileNotFoundError FileExistsError TimeoutError ConnectionError BrokenPipeError
EOFError MemoryError RecursionError UnicodeError UnicodeDecodeError
ExceptionGroup BaseExceptionGroup KeyboardInterrupt SystemExit StopIteration
ValidationError PydanticSerializationError ResponseValidationError
RequestValidationError HTTPException PublicWebAppURLConfigurationError
TelegramDeliveryRuntimeConfigurationError SQLAlchemyError DBAPIError
IntegrityError OperationalError ProgrammingError InterfaceError DataError
StatementError MissingGreenlet PendingRollbackError InvalidRequestError
NoResultFound MultipleResultsFound StaleDataError DisconnectionError CompileError
PostgresError UndefinedTableError UndefinedColumnError ForeignKeyViolationError
UniqueViolationError NotNullViolationError InvalidTextRepresentationError
NumericValueOutOfRangeError SerializationError DeadlockDetectedError
InsufficientPrivilegeError InFailedSQLTransactionError ConnectionDoesNotExistError
CannotConnectNowError
""".split())
HEADERS = (b'Traceback (most recent call last):', b'Exception Group Traceback (most recent call last):')
FRAME = re.compile(rb'^File "([^"\r\n]{1,256})", line ([1-9][0-9]{0,6})(?:, in [^\r\n]*)?$')
EXCEPTION = re.compile(rb'^(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]*)(?::|$)')


def extract(stream):
    """Read at most the physical limits; retain only fixed-enum metadata."""
    result = {
        'schema': 'browser_ci_backend_exception_metadata/1', 'status': 'complete',
        'input_bytes': 0, 'input_lines': 0, 'input_complete': False,
        'truncation': [], 'incomplete_tracebacks': 0, 'reports': [],
        'input_error_class': None,
    }
    frames = deque(maxlen=MAX_FRAMES)
    omitted = 0
    active = False
    try:
        while True:
            # The one-byte lookahead detects overflow without reading an
            # unbounded line or consuming the rest of an overflowing stream.
            size = min(MAX_LINE_BYTES + 1, MAX_INPUT_BYTES - result['input_bytes'] + 1)
            raw = stream.readline(size)
            if not raw:
                result['input_complete'] = True
                break
            if type(raw) is not bytes or len(raw) > size:
                raise TypeError('bounded_binary_stream_required')
            result['input_bytes'] += len(raw)
            result['input_lines'] += 1
            if result['input_bytes'] > MAX_INPUT_BYTES:
                result['truncation'].append('input_bytes')
                break
            if len(raw) > MAX_LINE_BYTES:
                result['truncation'].append('line_bytes')
                break
            line = raw.rstrip(b'\r\n').lstrip(b' +-|')
            if line in HEADERS:
                result['incomplete_tracebacks'] += int(active)
                active = True
                frames.clear()
                omitted = 0
                continue
            if not active:
                continue
            match = FRAME.fullmatch(line)
            if match:
                path = match[1][5:] if match[1].startswith(b'/app/') else b''
                try:
                    relative = path.decode('ascii')
                except UnicodeDecodeError:
                    relative = ''
                if relative in APPROVED_PATHS:
                    omitted += int(len(frames) == MAX_FRAMES)
                    frames.append({'path': relative, 'line': int(match[2])})
                else:
                    omitted += 1
                continue
            match = EXCEPTION.match(line)
            if match is None:
                continue
            name = match[1].decode('ascii')
            if name not in CLOSED_CLASSES and not name.endswith(('Error', 'Exception')):
                continue
            if len(result['reports']) == MAX_REPORTS:
                result['truncation'].append('report_count')
                break
            result['reports'].append({
                'exception_class': name if name in CLOSED_CLASSES else 'other',
                'frames': list(frames), 'frames_omitted': omitted,
            })
            active = False
            frames.clear()
            omitted = 0
    except Exception as error:
        name = type(error).__name__
        result['input_error_class'] = name if name in CLOSED_CLASSES else 'other'
        result['status'] = 'input_unavailable'
    result['incomplete_tracebacks'] += int(active)
    if result['truncation']:
        result['status'] = 'truncated'
    return result


def main(argv=None, stdin=None, stdout=None):
    argv = sys.argv[1:] if argv is None else argv
    source = sys.stdin.buffer if stdin is None else stdin
    target = sys.stdout.buffer if stdout is None else stdout
    result = extract(source) if not argv else {
        'schema': 'browser_ci_backend_exception_metadata/1',
        'status': 'invalid_arguments', 'reports': [],
    }
    # Fixed cardinalities and exact public names bound serialization too.
    raw = (json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(raw) > MAX_OUTPUT_BYTES:
        raw = b'{"schema":"browser_ci_backend_exception_metadata/1","status":"output_bound","reports":[]}\n'
        result['status'] = 'output_bound'
    try:
        if target.write(raw) != len(raw):
            return 1
        target.flush()
    except Exception:
        return 1
    return 0 if result['status'] in ('complete', 'truncated') else 1


if __name__ == '__main__':
    raise SystemExit(main())
