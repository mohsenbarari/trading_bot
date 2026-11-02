
def apply_test_fence(stack):
    import os
    from unittest.mock import patch
    denied=lambda *a,**k:(_ for _ in ()).throw(AssertionError('ambient_host_network_denied'))
    actual_kill=os.kill
    own_pid=os.getpid()
    def own_liveness(pid, sig):
        if type(pid) is int and pid == own_pid and type(sig) is int and sig == 0:
            return actual_kill(pid, sig)
        return denied(pid, sig)
    stack.enter_context(patch('os.kill',side_effect=own_liveness))
    for target in ('socket.socket.connect','socket.socket.connect_ex','socket.socket.bind',
                   'socket.socket.listen','socket.socket.accept','socket.socket.sendto','socket.socket.sendmsg',
                   'socket.create_connection','socket.getaddrinfo','subprocess.run','subprocess.Popen',
                   'os.system','os.killpg','urllib.request.urlopen','urllib.request.OpenerDirector.open',
                   'ctypes.CDLL','ctypes.PyDLL','resource.setrlimit','signal.signal','signal.alarm'):
        stack.enter_context(patch(target,side_effect=denied))
