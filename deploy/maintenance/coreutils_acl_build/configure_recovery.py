"""Opt-in public configure recovery; no package/vendor build or app operation.

The old host/sourcebuild bodies and package lock remain immutable. The two
embedded GNU ACL headers retain their copyright/license notices verbatim.
"""
from pathlib import Path
from hashlib import sha256
from types import ModuleType,SimpleNamespace
import argparse,base64,errno,fcntl,json,os,re,shlex,stat,time
CONFIRM='ROOT-COREUTILS-CONFIGURE-ONLY-EXACT-HEADER-20261029-01'
HOST_PIN=(53351,'fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82')
SOURCE_PIN=(63436,'d4f727ac1b581aaed4aa3534fe66713704c1fe0f1171b3e028930138c56b275d')
HEADER_PIN=(184650,'807424a82381273957b5bacc7fef16b5234ef2402e09722873e5a6e018283a9d')
LIB_PIN=(42928,'32d112de5fb4ec3124c7a4b859b738b45e4c4a9db3b3115f167625f082909dd1')
RUNTIME_PIN=(37076,'bbb12e190bfe19b876f73a669848ebd8d04293d95000babf51058c88879966b1')
SDK_HEADERS={'/usr/include/sys/acl.h': {'bytes': 3915, 'sha256': '01efa41d8ee0061085ee590908ccdd65d57c64d3f84f96a473620658cadb581c', 'base64': 'LyoKICBGaWxlOiBzeXMvYWNsLmgKCiAgKEMpIDE5OTkgQW5kcmVhcyBHcnVlbmJhY2hlciwgPGEuZ3J1ZW5iYWNoZXJAY29tcHV0ZXIub3JnPgoKICBUaGlzIGxpYnJhcnkgaXMgZnJlZSBzb2Z0d2FyZTsgeW91IGNhbiByZWRpc3RyaWJ1dGUgaXQgYW5kL29yCiAgbW9kaWZ5IGl0IHVuZGVyIHRoZSB0ZXJtcyBvZiB0aGUgR05VIExlc3NlciBHZW5lcmFsIFB1YmxpYwogIExpY2Vuc2UgYXMgcHVibGlzaGVkIGJ5IHRoZSBGcmVlIFNvZnR3YXJlIEZvdW5kYXRpb247IGVpdGhlcgogIHZlcnNpb24gMi4xIG9mIHRoZSBMaWNlbnNlLCBvciAoYXQgeW91ciBvcHRpb24pIGFueSBsYXRlciB2ZXJzaW9uLgoKICBUaGlzIGxpYnJhcnkgaXMgZGlzdHJpYnV0ZWQgaW4gdGhlIGhvcGUgdGhhdCBpdCB3aWxsIGJlIHVzZWZ1bCwKICBidXQgV0lUSE9VVCBBTlkgV0FSUkFOVFk7IHdpdGhvdXQgZXZlbiB0aGUgaW1wbGllZCB3YXJyYW50eSBvZgogIE1FUkNIQU5UQUJJTElUWSBvciBGSVRORVNTIEZPUiBBIFBBUlRJQ1VMQVIgUFVSUE9TRS4gIFNlZSB0aGUgR05VCiAgTGVzc2VyIEdlbmVyYWwgUHVibGljIExpY2Vuc2UgZm9yIG1vcmUgZGV0YWlscy4KCiAgWW91IHNob3VsZCBoYXZlIHJlY2VpdmVkIGEgY29weSBvZiB0aGUgR05VIExlc3NlciBHZW5lcmFsIFB1YmxpYwogIExpY2Vuc2UgYWxvbmcgd2l0aCB0aGlzIGxpYnJhcnk7IGlmIG5vdCwgc2VlIDxodHRwczovL3d3dy5nbnUub3JnL2xpY2Vuc2VzLz4uCiovCgojaWZuZGVmIF9fU1lTX0FDTF9ICiNkZWZpbmUgX19TWVNfQUNMX0gKCiNpbmNsdWRlIDxzeXMvdHlwZXMuaD4KCiNpZmRlZiBfX2NwbHVzcGx1cwpleHRlcm4gIkMiIHsKI2VuZGlmCgovKj09PSBEYXRhIHR5cGVzID09PSovCgpzdHJ1Y3QgX19hY2xfZXh0OwpzdHJ1Y3QgX19hY2xfZW50cnlfZXh0OwpzdHJ1Y3QgX19hY2xfcGVybXNldF9leHQ7Cgp0eXBlZGVmIHVuc2lnbmVkIGludAkJYWNsX3R5cGVfdDsKdHlwZWRlZiBpbnQJCQlhY2xfdGFnX3Q7CnR5cGVkZWYgdW5zaWduZWQgaW50CQlhY2xfcGVybV90OwoKdHlwZWRlZiBzdHJ1Y3QgX19hY2xfZXh0CSphY2xfdDsKdHlwZWRlZiBzdHJ1Y3QgX19hY2xfZW50cnlfZXh0CSphY2xfZW50cnlfdDsKdHlwZWRlZiBzdHJ1Y3QgX19hY2xfcGVybXNldF9leHQgKmFjbF9wZXJtc2V0X3Q7CgovKj09PSBDb25zdGFudHMgPT09Ki8KCi8qIDIzLjIuMiBhY2xfcGVybV90IHZhbHVlcyAqLwoKI2RlZmluZSBBQ0xfUkVBRAkJKDB4MDQpCiNkZWZpbmUgQUNMX1dSSVRFCQkoMHgwMikKI2RlZmluZSBBQ0xfRVhFQ1VURQkJKDB4MDEpCgovKiAyMy4yLjUgYWNsX3RhZ190IHZhbHVlcyAqLwoKI2RlZmluZSBBQ0xfVU5ERUZJTkVEX1RBRwkoMHgwMCkKI2RlZmluZSBBQ0xfVVNFUl9PQkoJCSgweDAxKQojZGVmaW5lIEFDTF9VU0VSCQkoMHgwMikKI2RlZmluZSBBQ0xfR1JPVVBfT0JKCQkoMHgwNCkKI2RlZmluZSBBQ0xfR1JPVVAJCSgweDA4KQojZGVmaW5lIEFDTF9NQVNLCQkoMHgxMCkKI2RlZmluZSBBQ0xfT1RIRVIJCSgweDIwKQoKLyogMjMuMy42IGFjbF90eXBlX3QgdmFsdWVzICovCgojZGVmaW5lIEFDTF9UWVBFX0FDQ0VTUwkJKDB4ODAwMCkKI2RlZmluZSBBQ0xfVFlQRV9ERUZBVUxUCSgweDQwMDApCgovKiAyMy4yLjcgQUNMIHF1YWxpZmllciBjb25zdGFudHMgKi8KCiNkZWZpbmUgQUNMX1VOREVGSU5FRF9JRAkoKGlkX3QpLTEpCgovKiAyMy4yLjggQUNMIEVudHJ5IENvbnN0YW50cyAqLwoKI2RlZmluZSBBQ0xfRklSU1RfRU5UUlkJCTAKI2RlZmluZSBBQ0xfTkVYVF9FTlRSWQkJMQoKLyo9PT0gQUNMIG1hbmlwdWxhdGlvbiA9PT0qLwoKZXh0ZXJuIGFjbF90IGFjbF9pbml0KGludCBjb3VudCk7CmV4dGVybiBhY2xfdCBhY2xfZHVwKGFjbF90IGFjbCk7CmV4dGVybiBpbnQgYWNsX2ZyZWUodm9pZCAqb2JqX3ApOwpleHRlcm4gaW50IGFjbF92YWxpZChhY2xfdCBhY2wpOwoKLyo9PT0gRW50cnkgbWFuaXB1bGF0aW9uID09PSovCgpleHRlcm4gaW50CmFjbF9jb3B5X2VudHJ5KGFjbF9lbnRyeV90IGRlc3RfZCwgYWNsX2VudHJ5X3Qgc3JjX2QpOwpleHRlcm4gaW50IGFjbF9jcmVhdGVfZW50cnkoYWNsX3QgKmFjbF9wLCBhY2xfZW50cnlfdCAqZW50cnlfcCk7CmV4dGVybiBpbnQgYWNsX2RlbGV0ZV9lbnRyeShhY2xfdCBhY2wsIGFjbF9lbnRyeV90IGVudHJ5X2QpOwpleHRlcm4gaW50IGFjbF9nZXRfZW50cnkoYWNsX3QgYWNsLCBpbnQgZW50cnlfaWQsIGFjbF9lbnRyeV90ICplbnRyeV9wKTsKCi8qIE1hbmlwdWxhdGUgQUNMIGVudHJ5IHBlcm1pc3Npb25zICovCgpleHRlcm4gaW50IGFjbF9hZGRfcGVybShhY2xfcGVybXNldF90IHBlcm1zZXRfZCwgYWNsX3Blcm1fdCBwZXJtKTsKZXh0ZXJuIGludCBhY2xfY2FsY19tYXNrKGFjbF90ICphY2xfcCk7CmV4dGVybiBpbnQgYWNsX2NsZWFyX3Blcm1zKGFjbF9wZXJtc2V0X3QgcGVybXNldF9kKTsKZXh0ZXJuIGludCBhY2xfZGVsZXRlX3Blcm0oYWNsX3Blcm1zZXRfdCBwZXJtc2V0X2QsIGFjbF9wZXJtX3QgcGVybSk7CmV4dGVybiBpbnQgYWNsX2dldF9wZXJtc2V0KGFjbF9lbnRyeV90IGVudHJ5X2QsIGFjbF9wZXJtc2V0X3QgKnBlcm1zZXRfcCk7CmV4dGVybiBpbnQgYWNsX3NldF9wZXJtc2V0KGFjbF9lbnRyeV90IGVudHJ5X2QsIGFjbF9wZXJtc2V0X3QgcGVybXNldF9kKTsKCi8qIE1hbmlwdWxhdGUgQUNMIGVudHJ5IHRhZyB0eXBlIGFuZCBxdWFsaWZpZXIgKi8KCmV4dGVybiB2b2lkICogYWNsX2dldF9xdWFsaWZpZXIoYWNsX2VudHJ5X3QgZW50cnlfZCk7CmV4dGVybiBpbnQgYWNsX2dldF90YWdfdHlwZShhY2xfZW50cnlfdCBlbnRyeV9kLCBhY2xfdGFnX3QgKnRhZ190eXBlX3ApOwpleHRlcm4gaW50IGFjbF9zZXRfcXVhbGlmaWVyKGFjbF9lbnRyeV90IGVudHJ5X2QsIGNvbnN0IHZvaWQgKnRhZ19xdWFsaWZpZXJfcCk7CmV4dGVybiBpbnQgYWNsX3NldF90YWdfdHlwZShhY2xfZW50cnlfdCBlbnRyeV9kLCBhY2xfdGFnX3QgdGFnX3R5cGUpOwoKLyo9PT0gRm9ybWF0IHRyYW5zbGF0aW9uID09PSovCgpleHRlcm4gc3NpemVfdCBhY2xfY29weV9leHQodm9pZCAqYnVmX3AsIGFjbF90IGFjbCwgc3NpemVfdCBzaXplKTsKZXh0ZXJuIGFjbF90IGFjbF9jb3B5X2ludChjb25zdCB2b2lkICpidWZfcCk7CmV4dGVybiBhY2xfdCBhY2xfZnJvbV90ZXh0KGNvbnN0IGNoYXIgKmJ1Zl9wKTsKZXh0ZXJuIHNzaXplX3QgYWNsX3NpemUoYWNsX3QgYWNsKTsKZXh0ZXJuIGNoYXIgKmFjbF90b190ZXh0KGFjbF90IGFjbCwgc3NpemVfdCAqbGVuX3ApOwoKLyo9PT0gT2JqZWN0IG1hbmlwdWxhdGlvbiA9PT0qLwoKZXh0ZXJuIGludCBhY2xfZGVsZXRlX2RlZl9maWxlKGNvbnN0IGNoYXIgKnBhdGhfcCk7CmV4dGVybiBpbnQgYWNsX2RlbGV0ZV9kZWZfZmlsZV9hdChpbnQgZGlyZmQsIGNvbnN0IGNoYXIgKnBhdGhfcCwgaW50IGF0X2ZsYWdzKTsKZXh0ZXJuIGFjbF90IGFjbF9nZXRfZmQoaW50IGZkKTsKZXh0ZXJuIGFjbF90IGFjbF9nZXRfZmlsZShjb25zdCBjaGFyICpwYXRoX3AsIGFjbF90eXBlX3QgdHlwZSk7CmV4dGVybiBhY2xfdCBhY2xfZ2V0X2ZpbGVfYXQoaW50IGRpcmZkLCBjb25zdCBjaGFyICpwYXRoX3AsIGludCBhdF9mbGFncywgYWNsX3R5cGVfdCB0eXBlKTsKZXh0ZXJuIGludCBhY2xfc2V0X2ZkKGludCBmZCwgYWNsX3QgYWNsKTsKZXh0ZXJuIGludCBhY2xfc2V0X2ZpbGUoY29uc3QgY2hhciAqcGF0aF9wLCBhY2xfdHlwZV90IHR5cGUsIGFjbF90IGFjbCk7CmV4dGVybiBpbnQgYWNsX3NldF9maWxlX2F0KGludCBkaXJmZCwgY29uc3QgY2hhciAqcGF0aF9wLCBpbnQgYXRfZmxhZ3MsIGFjbF90eXBlX3QgdHlwZSwKCQkJICAgYWNsX3QgYWNsKTsKCiNpZmRlZiBfX2NwbHVzcGx1cwp9CiNlbmRpZgoKI2VuZGlmICAvKiBfX1NZU19BQ0xfSCAqLwoK'}, '/usr/include/acl/libacl.h': {'bytes': 2703, 'sha256': '324dc3c9f6c6668d09b48f755aab7f00cc5d5d9e46780b18591e336875975962', 'base64': 'LyoKICBGaWxlOiBsaWJhY2wuaAoKICAoQykgMTk5OSwgMjAwMCBBbmRyZWFzIEdydWVuYmFjaGVyLCA8YS5ncnVlbmJhY2hlckBjb21wdXRlci5vcmc+CgogIFRoaXMgbGlicmFyeSBpcyBmcmVlIHNvZnR3YXJlOyB5b3UgY2FuIHJlZGlzdHJpYnV0ZSBpdCBhbmQvb3IKICBtb2RpZnkgaXQgdW5kZXIgdGhlIHRlcm1zIG9mIHRoZSBHTlUgTGVzc2VyIEdlbmVyYWwgUHVibGljCiAgTGljZW5zZSBhcyBwdWJsaXNoZWQgYnkgdGhlIEZyZWUgU29mdHdhcmUgRm91bmRhdGlvbjsgZWl0aGVyCiAgdmVyc2lvbiAyLjEgb2YgdGhlIExpY2Vuc2UsIG9yIChhdCB5b3VyIG9wdGlvbikgYW55IGxhdGVyIHZlcnNpb24uCgogIFRoaXMgbGlicmFyeSBpcyBkaXN0cmlidXRlZCBpbiB0aGUgaG9wZSB0aGF0IGl0IHdpbGwgYmUgdXNlZnVsLAogIGJ1dCBXSVRIT1VUIEFOWSBXQVJSQU5UWTsgd2l0aG91dCBldmVuIHRoZSBpbXBsaWVkIHdhcnJhbnR5IG9mCiAgTUVSQ0hBTlRBQklMSVRZIG9yIEZJVE5FU1MgRk9SIEEgUEFSVElDVUxBUiBQVVJQT1NFLiAgU2VlIHRoZSBHTlUKICBMZXNzZXIgR2VuZXJhbCBQdWJsaWMgTGljZW5zZSBmb3IgbW9yZSBkZXRhaWxzLgoKICBZb3Ugc2hvdWxkIGhhdmUgcmVjZWl2ZWQgYSBjb3B5IG9mIHRoZSBHTlUgTGVzc2VyIEdlbmVyYWwgUHVibGljCiAgTGljZW5zZSBhbG9uZyB3aXRoIHRoaXMgbGlicmFyeTsgaWYgbm90LCBzZWUgPGh0dHBzOi8vd3d3LmdudS5vcmcvbGljZW5zZXMvPi4KKi8KCiNpZm5kZWYgX19BQ0xfTElCQUNMX0gKI2RlZmluZSBfX0FDTF9MSUJBQ0xfSAoKI2luY2x1ZGUgPHN5cy9hY2wuaD4KCiNpZmRlZiBfX2NwbHVzcGx1cwpleHRlcm4gIkMiIHsKI2VuZGlmCgovKiBGbGFncyBmb3IgYWNsX3RvX2FueV90ZXh0KCkgKi8KCi8qIFByaW50IE5PLCBTT01FIG9yIEFMTCBlZmZlY3RpdmUgcGVybWlzc2lvbnMgY29tbWVudHMuIFNPTUUgcHJpbnRzCiAgIGVmZmVjdGl2ZSByaWdodHMgY29tbWVudHMgZm9yIGVudHJpZXMgd2hpY2ggaGF2ZSBkaWZmZXJlbnQgcGVybWlzc2lvbnMKICAgdGhhbiBlZmZlY3RpdmUgcGVybWlzc2lvbnMuICAqLwojZGVmaW5lIFRFWFRfU09NRV9FRkZFQ1RJVkUJCTB4MDEKI2RlZmluZSBURVhUX0FMTF9FRkZFQ1RJVkUJCTB4MDIKCi8qIEFsaWduIGVmZmVjdGl2ZSBwZXJtaXNzaW9uIGNvbW1lbnRzIHRvIGNvbHVtbiAzMiB1c2luZyB0YWJzIG9yCiAgIHVzZSBhIHNpbmdsZSB0YWIuICovCiNkZWZpbmUgVEVYVF9TTUFSVF9JTkRFTlQJCTB4MDQKCi8qIFVzZXIgYW5kIGdyb3VwIElEcyBpbnN0ZWFkIG9mIG5hbWVzLiAqLwojZGVmaW5lIFRFWFRfTlVNRVJJQ19JRFMJCTB4MDgKCi8qIE9ubHkgb3V0cHV0IHRoZSBmaXJzdCBsZXR0ZXIgb2YgZW50cnkgdHlwZXMKICAgKCJ1Ojpyd3giIGluc3RlYWQgb2YgInVzZXI6OnJ3eCIpLiAqLwojZGVmaW5lIFRFWFRfQUJCUkVWSUFURQkJCTB4MTAKCi8qIGFjbF9jaGVjayBlcnJvciBjb2RlcyAqLwoKI2RlZmluZSBBQ0xfTVVMVElfRVJST1IJCSgweDEwMDApICAgICAvKiBtdWx0aXBsZSB1bmlxdWUgb2JqZWN0cyAqLwojZGVmaW5lIEFDTF9EVVBMSUNBVEVfRVJST1IJKDB4MjAwMCkgICAgIC8qIGR1cGxpY2F0ZSBJZCdzIGluIGVudHJpZXMgKi8KI2RlZmluZSBBQ0xfTUlTU19FUlJPUgkJKDB4MzAwMCkgICAgIC8qIG1pc3NpbmcgcmVxdWlyZWQgZW50cnkgKi8KI2RlZmluZSBBQ0xfRU5UUllfRVJST1IJCSgweDQwMDApICAgICAvKiB3cm9uZyBlbnRyeSB0eXBlICovCgpleHRlcm4gY2hhciAqYWNsX3RvX2FueV90ZXh0KGFjbF90IGFjbCwgY29uc3QgY2hhciAqcHJlZml4LAoJCQkgICAgIGNoYXIgc2VwYXJhdG9yLCBpbnQgb3B0aW9ucyk7CmV4dGVybiBpbnQgYWNsX2NtcChhY2xfdCBhY2wxLCBhY2xfdCBhY2wyKTsKZXh0ZXJuIGludCBhY2xfY2hlY2soYWNsX3QgYWNsLCBpbnQgKmxhc3QpOwpleHRlcm4gYWNsX3QgYWNsX2Zyb21fbW9kZShtb2RlX3QgbW9kZSk7CmV4dGVybiBpbnQgYWNsX2VxdWl2X21vZGUoYWNsX3QgYWNsLCBtb2RlX3QgKm1vZGVfcCk7CmV4dGVybiBpbnQgYWNsX2V4dGVuZGVkX2ZpbGUoY29uc3QgY2hhciAqcGF0aF9wKTsKZXh0ZXJuIGludCBhY2xfZXh0ZW5kZWRfZmlsZV9ub2ZvbGxvdyhjb25zdCBjaGFyICpwYXRoX3ApOwpleHRlcm4gaW50IGFjbF9leHRlbmRlZF9maWxlX2F0KGludCBkaXJmZCwgY29uc3QgY2hhciAqcGF0aF9wLCBpbnQgYXRfZmxhZ3MpOwpleHRlcm4gaW50IGFjbF9leHRlbmRlZF9mZChpbnQgZmQpOwpleHRlcm4gaW50IGFjbF9lbnRyaWVzKGFjbF90IGFjbCk7CmV4dGVybiBjb25zdCBjaGFyICphY2xfZXJyb3IoaW50IGNvZGUpOwpleHRlcm4gaW50IGFjbF9nZXRfcGVybShhY2xfcGVybXNldF90IHBlcm1zZXRfZCwgYWNsX3Blcm1fdCBwZXJtKTsKCi8qIENvcHlpbmcgcGVybWlzc2lvbnMgYmV0d2VlbiBmaWxlcyAqLwpzdHJ1Y3QgZXJyb3JfY29udGV4dDsKZXh0ZXJuIGludCBwZXJtX2NvcHlfZmlsZSAoY29uc3QgY2hhciAqLCBjb25zdCBjaGFyICosCgkJCSAgICBzdHJ1Y3QgZXJyb3JfY29udGV4dCAqKTsKZXh0ZXJuIGludCBwZXJtX2NvcHlfZmQgKGNvbnN0IGNoYXIgKiwgaW50LCBjb25zdCBjaGFyICosIGludCwKCQkJICBzdHJ1Y3QgZXJyb3JfY29udGV4dCAqKTsKCiNpZmRlZiBfX2NwbHVzcGx1cwp9CiNlbmRpZgoKI2VuZGlmICAvKiBfX0FDTF9MSUJBQ0xfSCAqLwoK'}}
BUILDINFO='Rm9ybWF0OiAxLjAKU291cmNlOiBjb3JldXRpbHMKQmluYXJ5OiBjb3JldXRpbHMgY29yZXV0aWxzLWRiZ3N5bQpBcmNoaXRlY3R1cmU6IGFtZDY0ClZlcnNpb246IDkuNy0zK2dvbGQyCkNoZWNrc3Vtcy1NZDU6CiA2NzE1MDRmZDYzNWJmOTAxYjhmMjUxYWM5ZjdkNzkyMCA3MzU0MDMyIGNvcmV1dGlscy1kYmdzeW1fOS43LTMrZ29sZDJfYW1kNjQuZGViCiAzYjkyOGY5OGUyY2FjNjFkNTAyMzBkNTE2OTY1ODU3YSAzMDMwNTY4IGNvcmV1dGlsc185LjctMytnb2xkMl9hbWQ2NC5kZWIKQ2hlY2tzdW1zLVNoYTE6CiA3NzFmZTA4ZjI1ZTIxZTdjN2QwOTZlZWY1YmViNmUyNjYwNzJjMTY4IDczNTQwMzIgY29yZXV0aWxzLWRiZ3N5bV85LjctMytnb2xkMl9hbWQ2NC5kZWIKIGIxODQ2ZDM3OWI0ODZjMzFkMzQwMGU2YjNjMjg4YTNiZTRkZjlmMDMgMzAzMDU2OCBjb3JldXRpbHNfOS43LTMrZ29sZDJfYW1kNjQuZGViCkNoZWNrc3Vtcy1TaGEyNTY6CiBkYjJiNDRjZDViNTk0YjFhMWYyOTA4NWNkMWIzNjcxOGQyZDI1OGZkODExNjk0YmE3YjlmZDY4NDhjZGFkNWU3IDczNTQwMzIgY29yZXV0aWxzLWRiZ3N5bV85LjctMytnb2xkMl9hbWQ2NC5kZWIKIGRjMTY5YzVlNzIyM2FmM2IwMDAxNDAzNDAwZTRjNmFlNWJlNGIwODlhNTJlZTUwYzZiM2Q3ZDQwNDlkYzVlZDEgMzAzMDU2OCBjb3JldXRpbHNfOS43LTMrZ29sZDJfYW1kNjQuZGViCkJ1aWxkLU9yaWdpbjogRGViaWFuCkJ1aWxkLUFyY2hpdGVjdHVyZTogYW1kNjQKQnVpbGQtRGF0ZTogVGh1LCAyOSBPY3QgMjAyNiAwNzozNDoxNiArMDAwMApCdWlsZC1QYXRoOiAvYnVpbGQvY29yZXV0aWxzLWFjbC9jb3JldXRpbHMvc291cmNlCkJ1aWxkLVRhaW50ZWQtQnk6CiB1c3ItbG9jYWwtaGFzLWluY2x1ZGVzCiB1c3ItbG9jYWwtaGFzLWxpYnJhcmllcwogdXNyLWxvY2FsLWhhcy1wcm9ncmFtcwpJbnN0YWxsZWQtQnVpbGQtRGVwZW5kczoKIGF1dG9jb25mICg9IDIuNzItMy4xKSwKIGF1dG9tYWtlICg9IDE6MS4xNy00KSwKIGF1dG9wb2ludCAoPSAwLjIzLjEtMiksCiBhdXRvdG9vbHMtZGV2ICg9IDIwMjQwNzI3LjEpLAogYmFzZS1maWxlcyAoPSAxMy44K2RlYjEzdTcpLAogYmFzZS1wYXNzd2QgKD0gMy42LjcpLAogYmFzaCAoPSA1LjIuMzctMitiMTApLAogYmludXRpbHMgKD0gMi40NC0zKSwKIGJpbnV0aWxzLWNvbW1vbiAoPSAyLjQ0LTMpLAogYmludXRpbHMteDg2LTY0LWxpbnV4LWdudSAoPSAyLjQ0LTMpLAogYmlzb24gKD0gMjozLjguMitkZnNnLTErYjIpLAogYnNkZXh0cmF1dGlscyAoPSAyLjQxLjUtMCtkZWIxM3UxKSwKIGJzZHV0aWxzICg9IDE6Mi40MS41LTArZGViMTN1MSksCiBidWlsZC1lc3NlbnRpYWwgKD0gMTIuMTIpLAogYnppcDIgKD0gMS4wLjgtNiksCiBjb3JldXRpbHMgKD0gOS43LTMpLAogY3BwICg9IDQ6MTQuMi4wLTEpLAogY3BwLTE0ICg9IDE0LjIuMC0xOSksCiBjcHAtMTQteDg2LTY0LWxpbnV4LWdudSAoPSAxNC4yLjAtMTkpLAogY3BwLXg4Ni02NC1saW51eC1nbnUgKD0gNDoxNC4yLjAtMSksCiBkYXNoICg9IDAuNS4xMi0xMiksCiBkZWJjb25mICg9IDEuNS45MSksCiBkZWJoZWxwZXIgKD0gMTMuMjQuMiksCiBkZWJpYW51dGlscyAoPSA1LjIzLjIpLAogZGgtYXV0b3JlY29uZiAoPSAyMCksCiBkaC1zdHJpcC1ub25kZXRlcm1pbmlzbSAoPSAxLjE0LjEtMiksCiBkaWZmdXRpbHMgKD0gMTozLjEwLTQpLAogZHBrZyAoPSAxLjIyLjIyKSwKIGRwa2ctZGV2ICg9IDEuMjIuMjIpLAogZHd6ICg9IDAuMTUtMStiMSksCiBmaWxlICg9IDE6NS40Ni01KSwKIGZpbmR1dGlscyAoPSA0LjEwLjAtMyksCiBnKysgKD0gNDoxNC4yLjAtMSksCiBnKystMTQgKD0gMTQuMi4wLTE5KSwKIGcrKy0xNC14ODYtNjQtbGludXgtZ251ICg9IDE0LjIuMC0xOSksCiBnKysteDg2LTY0LWxpbnV4LWdudSAoPSA0OjE0LjIuMC0xKSwKIGdjYyAoPSA0OjE0LjIuMC0xKSwKIGdjYy0xNCAoPSAxNC4yLjAtMTkpLAogZ2NjLTE0LWJhc2UgKD0gMTQuMi4wLTE5KSwKIGdjYy0xNC14ODYtNjQtbGludXgtZ251ICg9IDE0LjIuMC0xOSksCiBnY2MteDg2LTY0LWxpbnV4LWdudSAoPSA0OjE0LjIuMC0xKSwKIGdldHRleHQgKD0gMC4yMy4xLTIpLAogZ2V0dGV4dC1iYXNlICg9IDAuMjMuMS0yKSwKIGdwZXJmICg9IDMuMi4xLTEpLAogZ3JlcCAoPSAzLjExLTQpLAogZ3JvZmYtYmFzZSAoPSAxLjIzLjAtOSksCiBnemlwICg9IDEuMTMtMStkZWIxM3UxKSwKIGhvc3RuYW1lICg9IDMuMjUpLAogaW5pdC1zeXN0ZW0taGVscGVycyAoPSAxLjY5fmRlYjEzdTEpLAogaW50bHRvb2wtZGViaWFuICg9IDAuMzUuMCsyMDA2MDcxMC42KSwKIGxpYmFjbDEgKD0gMi40LjAtMX50cml4aWUrZ29sZDEpLAogbGliYWNsMS1kZXYgKD0gMi40LjAtMX50cml4aWUrZ29sZDEpLAogbGliYXJjaGl2ZS16aXAtcGVybCAoPSAxLjY4LTEpLAogbGliYXNhbjggKD0gMTQuMi4wLTE5KSwKIGxpYmF0b21pYzEgKD0gMTQuMi4wLTE5KSwKIGxpYmF0dHIxICg9IDE6Mi41LjItMyksCiBsaWJhdHRyMS1kZXYgKD0gMToyLjUuMi0zKSwKIGxpYmF1ZGl0LWNvbW1vbiAoPSAxOjQuMC4yLTIrZGViMTN1MSksCiBsaWJhdWRpdDEgKD0gMTo0LjAuMi0yK2RlYjEzdTEpLAogbGliYmludXRpbHMgKD0gMi40NC0zKSwKIGxpYmJsa2lkMSAoPSAyLjQxLjUtMCtkZWIxM3UxKSwKIGxpYmJ6Mi0xLjAgKD0gMS4wLjgtNiksCiBsaWJjLWJpbiAoPSAyLjQxLTEyK2RlYjEzdTQpLAogbGliYy1kZXYtYmluICg9IDIuNDEtMTIrZGViMTN1NCksCiBsaWJjNiAoPSAyLjQxLTEyK2RlYjEzdTQpLAogbGliYzYtZGV2ICg9IDIuNDEtMTIrZGViMTN1NCksCiBsaWJjYXAtZGV2ICg9IDE6Mi43NS0xMCtkZWIxM3UxK2IzKSwKIGxpYmNhcC1uZzAgKD0gMC44LjUtNCtiMSksCiBsaWJjYXAyICg9IDE6Mi43NS0xMCtkZWIxM3UxK2IzKSwKIGxpYmNjMS0wICg9IDE0LjIuMC0xOSksCiBsaWJjcnlwdC1kZXYgKD0gMTo0LjQuMzgtMSksCiBsaWJjcnlwdDEgKD0gMTo0LjQuMzgtMSksCiBsaWJjdGYtbm9iZmQwICg9IDIuNDQtMyksCiBsaWJjdGYwICg9IDIuNDQtMyksCiBsaWJkYjUuM3Q2NCAoPSA1LjMuMjgrZGZzZzItOSksCiBsaWJkZWJjb25mY2xpZW50MCAoPSAwLjI4MCksCiBsaWJkZWJoZWxwZXItcGVybCAoPSAxMy4yNC4yKSwKIGxpYmRwa2ctcGVybCAoPSAxLjIyLjIyKSwKIGxpYmVsZjF0NjQgKD0gMC4xOTItNCksCiBsaWJmaWxlLXN0cmlwbm9uZGV0ZXJtaW5pc20tcGVybCAoPSAxLjE0LjEtMiksCiBsaWJnY2MtMTQtZGV2ICg9IDE0LjIuMC0xOSksCiBsaWJnY2MtczEgKD0gMTQuMi4wLTE5KSwKIGxpYmdkYm0tY29tcGF0NHQ2NCAoPSAxLjI0LTIpLAogbGliZ2RibTZ0NjQgKD0gMS4yNC0yKSwKIGxpYmdtcC1kZXYgKD0gMjo2LjMuMCtkZnNnLTMpLAogbGliZ21wMTAgKD0gMjo2LjMuMCtkZnNnLTMpLAogbGliZ21wMy1kZXYgKD0gMjo2LjMuMCtkZnNnLTMpLAogbGliZ21weHg0bGRibCAoPSAyOjYuMy4wK2Rmc2ctMyksCiBsaWJnb21wMSAoPSAxNC4yLjAtMTkpLAogbGliZ3Byb2ZuZzAgKD0gMi40NC0zKSwKIGxpYmh3YXNhbjAgKD0gMTQuMi4wLTE5KSwKIGxpYmlzbDIzICg9IDAuMjctMSksCiBsaWJpdG0xICg9IDE0LjIuMC0xOSksCiBsaWJqYW5zc29uNCAoPSAyLjE0LTIrYjMpLAogbGlibGFzdGxvZzItMiAoPSAyLjQxLjUtMCtkZWIxM3UxKSwKIGxpYmxzYW4wICg9IDE0LjIuMC0xOSksCiBsaWJsem1hNSAoPSA1LjguMS0xK2RlYjEzdTIpLAogbGlibWFnaWMtbWdjICg9IDE6NS40Ni01KSwKIGxpYm1hZ2ljMXQ2NCAoPSAxOjUuNDYtNSksCiBsaWJtZDAgKD0gMS4xLjAtMitiMSksCiBsaWJtb3VudDEgKD0gMi40MS41LTArZGViMTN1MSksCiBsaWJtcGMzICg9IDEuMy4xLTErYjMpLAogbGlibXBmcjYgKD0gNC4yLjItMSksCiBsaWJuY3Vyc2VzdzYgKD0gNi41KzIwMjUwMjE2LTIpLAogbGlicGFtLW1vZHVsZXMgKD0gMS43LjAtNSksCiBsaWJwYW0tbW9kdWxlcy1iaW4gKD0gMS43LjAtNSksCiBsaWJwYW0tcnVudGltZSAoPSAxLjcuMC01KSwKIGxpYnBhbTBnICg9IDEuNy4wLTUpLAogbGlicGNyZTItMTYtMCAoPSAxMC40Ni0xfmRlYjEzdTMpLAogbGlicGNyZTItMzItMCAoPSAxMC40Ni0xfmRlYjEzdTMpLAogbGlicGNyZTItOC0wICg9IDEwLjQ2LTF+ZGViMTN1MyksCiBsaWJwY3JlMi1kZXYgKD0gMTAuNDYtMX5kZWIxM3UzKSwKIGxpYnBjcmUyLXBvc2l4MyAoPSAxMC40Ni0xfmRlYjEzdTMpLAogbGlicGVybDUuNDAgKD0gNS40MC4xLTYrZGViMTN1MSksCiBsaWJwaXBlbGluZTEgKD0gMS41LjgtMSksCiBsaWJwcm9jMi0wICg9IDI6NC4wLjQtOSksCiBsaWJxdWFkbWF0aDAgKD0gMTQuMi4wLTE5KSwKIGxpYnNlY2NvbXAyICg9IDIuNi4wLTIpLAogbGlic2VsaW51eDEgKD0gMy44LjEtMSksCiBsaWJzZWxpbnV4MS1kZXYgKD0gMy44LjEtMSksCiBsaWJzZXBvbC1kZXYgKD0gMy44LjEtMSksCiBsaWJzZXBvbDIgKD0gMy44LjEtMSksCiBsaWJzZnJhbWUxICg9IDIuNDQtMyksCiBsaWJzbWFydGNvbHMxICg9IDIuNDEuNS0wK2RlYjEzdTEpLAogbGlic3FsaXRlMy0wICg9IDMuNDYuMS03K2RlYjEzdTIpLAogbGlic3NsLWRldiAoPSAzLjUuNy0xfmRlYjEzdTMpLAogbGlic3NsM3Q2NCAoPSAzLjUuNy0xfmRlYjEzdTMpLAogbGlic3RkYysrLTE0LWRldiAoPSAxNC4yLjAtMTkpLAogbGlic3RkYysrNiAoPSAxNC4yLjAtMTkpLAogbGlic3lzdGVtZC1kZXYgKD0gMjU3LjEzLTF+ZGViMTN1MSksCiBsaWJzeXN0ZW1kMCAoPSAyNTcuMTMtMX5kZWIxM3UxKSwKIGxpYnRleHQtY2hhcndpZHRoLXBlcmwgKD0gMC4wNC0xMStiNCksCiBsaWJ0ZXh0LXVuaWRlY29kZS1wZXJsICg9IDEuMzAtMyksCiBsaWJ0ZXh0LXdyYXBpMThuLXBlcmwgKD0gMC4wNi0xMCksCiBsaWJ0aW5mbzYgKD0gNi41KzIwMjUwMjE2LTIpLAogbGlidG9vbCAoPSAyLjUuNC00KSwKIGxpYnRzYW4yICg9IDE0LjIuMC0xOSksCiBsaWJ1YnNhbjEgKD0gMTQuMi4wLTE5KSwKIGxpYnVjaGFyZGV0MCAoPSAwLjAuOC0xK2IyKSwKIGxpYnVkZXYxICg9IDI1Ny4xMy0xfmRlYjEzdTEpLAogbGlidW5pc3RyaW5nNSAoPSAxLjMtMiksCiBsaWJ1dWlkMSAoPSAyLjQxLjUtMCtkZWIxM3UxKSwKIGxpYnhtbC1saWJ4bWwtcGVybCAoPSAyLjAyMDcrZGZzZytyZWFsbHkrMi4wMTM0LTUrZGViMTN1MSksCiBsaWJ4bWwtbmFtZXNwYWNlc3VwcG9ydC1wZXJsICg9IDEuMTItMiksCiBsaWJ4bWwtc2F4LWJhc2UtcGVybCAoPSAxLjA5LTMpLAogbGlieG1sLXNheC1wZXJsICg9IDEuMDIrZGZzZy00KSwKIGxpYnhtbDIgKD0gMi4xMi43K2Rmc2crcmVhbGx5Mi45LjE0LTIuMStkZWIxM3UzKSwKIGxpYnpzdGQxICg9IDEuNS43K2Rmc2ctMSksCiBsaW51eC1saWJjLWRldiAoPSA2LjEyLjExMy0xKSwKIG00ICg9IDEuNC4xOS04KSwKIG1ha2UgKD0gNC40LjEtMiksCiBtYW4tZGIgKD0gMi4xMy4xLTEpLAogbWF3ayAoPSAxLjMuNC4yMDI1MDEzMS0xKSwKIG5jdXJzZXMtYmFzZSAoPSA2LjUrMjAyNTAyMTYtMiksCiBuY3Vyc2VzLWJpbiAoPSA2LjUrMjAyNTAyMTYtMiksCiBvcGVuc3NsLXByb3ZpZGVyLWxlZ2FjeSAoPSAzLjUuNy0xfmRlYjEzdTMpLAogcGF0Y2ggKD0gMi44LTIpLAogcGVybCAoPSA1LjQwLjEtNitkZWIxM3UxKSwKIHBlcmwtYmFzZSAoPSA1LjQwLjEtNitkZWIxM3UxKSwKIHBlcmwtbW9kdWxlcy01LjQwICg9IDUuNDAuMS02K2RlYjEzdTEpLAogcG8tZGViY29uZiAoPSAxLjAuMjErbm11MSksCiBwcm9jcHMgKD0gMjo0LjAuNC05KSwKIHJwY3N2Yy1wcm90byAoPSAxLjQuMy0xKSwKIHNlZCAoPSA0LjktMitkZWIxM3UxKSwKIHNlbnNpYmxlLXV0aWxzICg9IDAuMC4yNSksCiBzeXN2aW5pdC11dGlscyAoPSAzLjE0LTQpLAogdGFyICg9IDEuMzUrZGZzZy0zLjEpLAogdGV4LWNvbW1vbiAoPSA2LjE5KSwKIHRleGluZm8gKD0gNy4xLjEtMSksCiB0ZXhpbmZvLWxpYiAoPSA3LjEuMS0xK2IxKSwKIHVjZiAoPSAzLjAwNTIpLAogdXRpbC1saW51eCAoPSAyLjQxLjUtMCtkZWIxM3UxKSwKIHh6LXV0aWxzICg9IDUuOC4xLTErZGViMTN1MiksCiB6bGliMWcgKD0gMToxLjMuZGZzZytyZWFsbHkxLjMuMS0xK2IxKQpFbnZpcm9ubWVudDoKIERFQl9CVUlMRF9PUFRJT05TPSJwYXJhbGxlbD0yIgogREVCX0JVSUxEX1BST0ZJTEVTPSJub3VkZWIiCiBMQU5HPSJDLlVURi04IgogTENfQUxMPSJDLlVURi04IgogU09VUkNFX0RBVEVfRVBPQ0g9IjE3OTMwNTkyMDAiCg=='
SOURCE_PRODUCTS={'coreutils_9.7-3.debian.tar.xz': (26820, '483f77876a080577f63da1d004cc1cf17d16df65d6925aefdd3294c6101eccfb'), 'coreutils_9.7-3.dsc': (2122, 'c3a207e3aaad165765c7a6fab89045f5fd20035fea6830b9f9ebbb92ebfbff89'), 'coreutils_9.7.orig.tar.xz': (6158960, 'e8bb26ad0293f9b5a1fc43fb42ba970e312c66ce92c1b0b16713d7500db251bf'), 'coreutils_9.7.orig.tar.xz.asc': (833, '5070e9428bd372a7fa1f66b395b81092bb40c98f9f56a2e2bc55c47fd387e901')}
RELEASE_PINS={'snapshot.debian.org_archive_debian-security_20261024T000000Z_dists_trixie-security_InRelease': (43409, 'f244a867a18dc1d246cc8712675f1592849f68c6c07be6914283e534b9418d3e'), 'snapshot.debian.org_archive_debian_20260820T000000Z_dists_forky_InRelease': (150579, '3f42c22f5f11f0176f1360354e59fc7c36084c8a238cf53fccf35dc09b75fdab'), 'snapshot.debian.org_archive_debian_20261024T000000Z_dists_trixie-updates_InRelease': (47340, '3d43d3271b819568b2a9ef49666c6c2038c91839cdbb98fe62de16db7eeee7c7'), 'snapshot.debian.org_archive_debian_20261024T000000Z_dists_trixie_InRelease': (140421, '0584fba32e13e0ab8285fb16c27adea1ec03a73669c18702821094fd6ca86675')}
BLOCK=65536;BODY_MAX=4*1024**2;OUTPUT_MAX=16*1024**2;RECEIPT_MAX=65536
GENERATED={'configure':'generated-configure.txt','Makefile.in':'generated-Makefile.in.txt','Makefile':'generated-Makefile.txt','lib/config.h':'generated-lib-config.h.txt','config.log':'observation-config.log.txt','config.status':'observation-config.status.txt','gnulib-tests/Makefile.in':'generated-gnulib-Makefile.in.txt','gnulib-tests/Makefile':'generated-gnulib-Makefile.txt'}
HISTORICAL={'Makefile': (2271754, 'e97166457f3c156d2264b7a2c94675173027e37d461bbf65608f6032f82de7f6'), 'Makefile.in': (2621315, '222a6f8994b53173cd4346274d965d80c0ec5364bb6e03ed5641637c2f8892ea'), 'config.log': (3407598, 'eb10c8ee0515fcabbf0b3d37f9e1a563b02e271690d2dd3d3122607737d1fa10'), 'config.status': (164951, '5752f160f03f72d7dba47ff6537e90fc54ba06b8960c955dbad3f8d1ec810403'), 'configure': (2399744, '8f93a17e204333bb6f0ce88405e7f3d7a5ed0b8ea9124fc7861440517f2ac40f'), 'gnulib-tests/Makefile': (693966, 'f4be32b6683f8ca69682623464d970e732be9e74a4d816115291ceffb9ff6427'), 'gnulib-tests/Makefile.in': (754595, '0e54a77bad92453472bb959d04bb8c01b084827c0d9c9cb752773904a043ae29'), 'lib/config.h': (184650, '807424a82381273957b5bacc7fef16b5234ef2402e09722873e5a6e018283a9d')}

def need(value,code):
 if not value:raise RuntimeError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def flags(fd):return(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))
def encode(value,maximum=RECEIPT_MAX):
 parts=[];count=0
 for part in json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(value):
  raw=part.encode();need(count+len(raw)+1<=maximum,'receipt_bound');count+=len(raw);parts.append(raw)
 return b''.join(parts)+b'\n'
class Held(RuntimeError):
 def __init__(self,state):self.state=state;super().__init__('configure_recovery_HELD')
class State:
 def __init__(self):self.end=time.monotonic()+3600;self.original_end=self.end;self.rows=[];self.errors=[];self.created=[]
 def note(self,e):
  if all(e is not x for x in self.errors):self.errors.append(e)
 def check(self):
  need(self.end==self.original_end and time.monotonic()<self.original_end,'original_deadline')
  for row in self.rows:row.check()
 def call(self,f,*a,**kw):
  primary=None
  try:need(not self.errors,'failed_scope');self.check();return f(*a,**kw)
  except BaseException as e:primary=e;self.note(e);raise
  finally:
   try:self.check()
   except BaseException as e:
    self.note(e)
    if primary is None:raise
class Loan:
 """New fixed-file inputs only; unknown or unadmitted close remains HELD."""
 def __init__(self,s,path,pin,owners=(0,),modes=(0o644,),maximum=BODY_MAX):
  self.state=s;self.path=Path(path).absolute();self.fd=self.original_fd=None;self.origin=None;self.before=self.fl=None;self.closed=self.attempted=self.admitted=self.uncertain=False;self.parents=();s.rows.append(self)
  try:
   need(type(pin)is tuple and type(pin[0])is int and 0<pin[0]<=maximum and re.fullmatch('[a-f0-9]{64}',pin[1]),'fixed_input_pin');self.pin=pin
   need(not s.errors,'failed_scope');s.check();self.parents=tuple((p,frame(p.lstat())[:5])for p in self.path.parents);need(all(stat.S_ISDIR(b[2])and b[3]in{0,1001,10001}and not b[2]&0o022 for p,b in self.parents),'input_parent_type');need(self.path.resolve(strict=True)==self.path,'input_path')
   b=frame(self.path.lstat());need(stat.S_ISREG(b[2])and stat.S_IMODE(b[2])in modes and b[3]in owners and b[4]in owners and b[5:7]==(1,pin[0]),'input_type')
   self.fd=self.original_fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);self.before=frame(os.fstat(self.fd));self.fl=flags(self.fd);self.origin=(self.path,self.original_fd,self.before,self.fl,self.parents)
   need(self.before==b and self.fl[0]&os.O_ACCMODE==os.O_RDONLY and self.fl[1]==fcntl.FD_CLOEXEC,'input_flags');self.check();raw=self.read();need((len(raw),sha256(raw).hexdigest())==pin,'input_hash');self.admitted=True
  except BaseException as e:self.uncertain=True;s.note(e);raise
 def check(self):
  try:
   need(not self.uncertain,'input_HELD')
   if self.before is None:return
   need((self.path,self.original_fd,self.before,self.fl,self.parents)==self.origin,'input_alias')
   need(self.path.resolve(strict=True)==self.path and frame(self.path.lstat())==self.before,'input_name')
   for p,b in self.parents:need(p.resolve(strict=True)==p and frame(p.lstat())[:5]==b,'input_parent')
   if not self.closed:need(self.fd==self.original_fd and frame(os.fstat(self.original_fd))==self.before and flags(self.original_fd)==self.fl and os.lseek(self.original_fd,0,os.SEEK_CUR)==0,'input_FD')
  except BaseException as e:self.uncertain=True;self.state.note(e);raise
 def read(self):return self._read(False)
 def _read(self,cleanup):
  need(not self.closed and(cleanup or not self.attempted)and(cleanup or not self.state.errors),'failed_or_closed_read');out=[];off=0
  while off<self.pin[0]:
   self.state.check()
   try:r=os.pread(self.original_fd,min(BLOCK,self.pin[0]-off),off)
   finally:self.state.check()
   need(r,'short_input');out.append(r);off+=len(r)
  raw=b''.join(out);need((len(raw),sha256(raw).hexdigest())==self.pin,'input_hash');return raw
 def close(self):
  need(not self.attempted,'close_replay');self.attempted=True
  if self.original_fd is None:self.closed=True;return
  try:
   self.check();need(self.admitted and not self.uncertain,'close_HELD');self._read(True);self.check();os.close(self.original_fd)
   try:os.fstat(self.original_fd)
   except OSError as e:need(e.errno==errno.EBADF,'close_unknown')
   else:raise RuntimeError('close_unknown')
   self.closed=True;self.fd=None;self.check()
  except BaseException as e:self.uncertain=True;self.state.note(e);raise

def load(s,path,pin,owners=(0,1001),modes=(0o644,)):
 row=Loan(s,path,pin,owners,modes);m=ModuleType('frozen_'+Path(path).stem);m.__file__=str(path);s.call(exec,compile(row.read(),str(path),'exec'),m.__dict__);return m

def finish(s):
 for row in reversed(s.rows):
  if row.attempted:continue
  try:row.close()
  except BaseException as e:s.note(e)
 try:s.check()
 except BaseException as e:s.note(e)
 if s.errors or not all(r.closed for r in s.rows):raise Held(s)from s.errors[0]if s.errors else None

class Writer:
 def __init__(self,s,path,mode):
  self.state=s;self.path=Path(path);self.fd=self.original_fd=None;self.before=self.fl=None;self.origin=None;self.parents=();self.closed=self.attempted=self.admitted=self.uncertain=False;self.written=0;s.rows.append(self)
  try:
   need(not s.errors,'failed_scope');s.check();self.parents=tuple((p,frame(p.lstat())[:5])for p in self.path.parents);need(all(stat.S_ISDIR(b[2])and b[3]in{0,1001,10001}and not b[2]&0o022 for p,b in self.parents),'writer_parent_type');self.fd=self.original_fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode);s.created.append(str(path));os.fchmod(self.fd,mode);self.before=frame(os.fstat(self.fd));self.fl=flags(self.fd);self.stable=self.before[:6];self.origin=(self.path,self.original_fd,self.fl,self.parents,self.stable);need(self.fl[0]&os.O_ACCMODE==os.O_RDWR and self.fl[1]==fcntl.FD_CLOEXEC,'writer_flags');need(self.before[2:7]==(stat.S_IFREG|mode,os.geteuid(),os.getegid(),1,0),'writer_type');self.admitted=True;self.check()
  except BaseException as e:self.uncertain=True;s.note(e);raise
 def check(self):
  try:
   need(not self.uncertain,'writer_HELD')
   if self.before is None:return
   need((self.path,self.original_fd,self.fl,self.parents,self.stable)==self.origin and self.before[:6]==self.stable,'writer_alias');need(self.path.resolve(strict=True)==self.path and frame(self.path.lstat())==self.before,'writer_name')
   for p,b in self.parents:need(p.resolve(strict=True)==p and frame(p.lstat())[:5]==b,'writer_parent')
   if not self.closed:need(self.fd==self.original_fd and frame(os.fstat(self.original_fd))==self.before and flags(self.original_fd)==self.fl and os.lseek(self.original_fd,0,os.SEEK_CUR)==self.written,'writer_FD')
  except BaseException as e:self.uncertain=True;self.state.note(e);raise
 def write(self,raw):
  try:
   need(not self.state.errors and not self.closed and not self.attempted,'failed_or_closed_write');self.state.check();old=self.before;n=os.write(self.original_fd,raw);current=frame(os.fstat(self.original_fd));need(0<n<=len(raw)and current[:6]==old[:6]and current[6]==old[6]+n and flags(self.original_fd)==self.fl and self.fd==self.original_fd,'writer_progress');self.written+=n;self.before=current;self.state.check();return n
  except BaseException as e:self.uncertain=True;self.state.note(e);raise
 def close(self):
  need(not self.attempted,'writer_close_replay');self.attempted=True
  if self.original_fd is None:self.closed=True;return
  try:
   self.check();need(self.admitted and not self.uncertain,'writer_close_HELD');os.close(self.original_fd)
   try:os.fstat(self.original_fd)
   except OSError as e:need(e.errno==errno.EBADF,'writer_close_unknown')
   else:raise RuntimeError('writer_close_unknown')
   self.fd=None;self.closed=True;self.check()
  except BaseException as e:self.uncertain=True;self.state.note(e);raise

def fixed_write(s,path,raw,mode=0o644,maximum=BODY_MAX):
 need(type(raw)is bytes and 0<len(raw)<=maximum and maximum<=OUTPUT_MAX,'output_bound');w=Writer(s,path,mode);off=0
 try:
  while off<len(raw):off+=w.write(raw[off:off+BLOCK])
  s.call(os.fsync,w.original_fd);w.check();r=Loan(s,path,(len(raw),sha256(raw).hexdigest()),owners=(os.geteuid(),),modes=(mode,),maximum=maximum);need(r.before==w.before and r.read()==raw,'output_readback');w.close();r.check();return{'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'mode':oct(mode)}
 except BaseException as e:s.note(e);raise

class NormalLink:
 def __init__(self,s,path,target):
  need(not s.errors,'failed_scope');s.check();self.state=s;self.path=path;self.target=target;self.before=frame(path.lstat());self.text=os.readlink(path);self.closed=self.attempted=self.uncertain=False;self.original_fd=None;s.rows.append(self);self.check()
 def check(self):
  try:need(not self.uncertain and stat.S_ISLNK(self.before[2])and frame(self.path.lstat())==self.before and os.readlink(self.path)==self.text and self.path.resolve(strict=True)==self.target,'normal_linker_binding')
  except BaseException as e:self.uncertain=True;self.state.note(e);raise
 def close(self):need(not self.attempted,'normal_link_replay');self.attempted=True;self.check();self.closed=True

def headers():
 rows={}
 for path,row in SDK_HEADERS.items():
  raw=base64.b64decode(row['base64'],validate=True);need((len(raw),sha256(raw).hexdigest())==(row['bytes'],row['sha256']),'embedded_header');rows[path]=raw
 need(set(rows)=={'/usr/include/sys/acl.h','/usr/include/acl/libacl.h'},'header_scope');return rows

def installed_dependencies(raw):
 text=raw.decode('ascii');start=text.index('Installed-Build-Depends:\n')+len('Installed-Build-Depends:\n');stop=text.index('\nEnvironment:',start);values=[]
 for line in text[start:stop].splitlines():
  line=line.strip().rstrip(',')
  if not line:continue
  m=re.fullmatch(r'([a-z0-9][a-z0-9+.-]*)(?::([a-z0-9]+))? \(= ([^\s()]+)\)',line);need(m is not None,'buildinfo_dependency');name=m[1]+(':'+m[2]if m[2]else'');values.append((name,m[3]))
 need(len(values)>100 and len(values)==len({v[0]for v in values}),'dependency_inventory');return values

def check_dependencies(S,deps):
 raw=S.metadata(['/usr/bin/dpkg-query','-W','-f=${binary:Package}\t${Version}\n',*[n for n,v in deps]])
 observed={}
 for line in raw.decode('ascii').splitlines():
  pieces=line.split('\t');need(len(pieces)==2,'package_state');observed[pieces[0]]=pieces[1]
 for name,version in deps:
  if name.split(':')[0]in{'libacl1','libacl1-dev'}:continue
  actual=observed.get(name,observed.get(name.split(':')[0]));need(actual==version,'exact_dependency_version')
 return observed

def manifest_script(H):
 expected='limits='+repr(H.OUTPUTS);need(H.MANIFEST.count(expected)==1,'actual_manifest_API');limits={leaf:BODY_MAX for leaf in GENERATED.values()};limits['configure-recovery.json']=RECEIPT_MAX
 return H.MANIFEST.replace(expected,'limits='+repr(limits),1)

def configure_commands():return[['/usr/bin/make','--no-print-directory','-f','debian/rules','override_dh_autoreconf'],['/usr/bin/make','--no-print-directory','-f','debian/rules','override_dh_auto_configure']]

def inner(args):
 need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0 and Path.cwd()==Path('/build')and Path('/.dockerenv').is_file(),'disposable_inner_Root');s=State();S=None;report=None
 try:
  Loan(s,Path(__file__).absolute(),(Path(__file__).stat().st_size,args.controller_sha256));S=load(s,Path('/recipe/sourcebuild.py'),SOURCE_PIN,owners=(0,),modes=(0o600,));S.builder_context()
  def command(argv,**kw):return s.call(S.command,argv,**kw)
  def metadata(argv,**kw):return s.call(S.metadata,argv,**kw)
  need(not S.WORK.exists()and not S.OUT.exists(),'fresh_inner_scope')
  deps=installed_dependencies(base64.b64decode(BUILDINFO,validate=True));hdr=headers()
  if args.inner_phase=='prepare':
   # Signed fixed snapshot, exact old dependency versions; no source SDK build.
   for p in Path('/etc/apt/sources.list.d').glob('*'):need(p.is_file()and not p.is_symlink(),'apt_file');p.unlink()
   Path('/etc/apt/sources.list').write_text('');stanzas='Types: deb deb-src\nURIs: https://snapshot.debian.org/archive/debian/20261024T000000Z/\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n\nTypes: deb deb-src\nURIs: https://snapshot.debian.org/archive/debian-security/20261024T000000Z/\nSuites: trixie-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n\nTypes: deb-src\nURIs: https://snapshot.debian.org/archive/debian/20260820T000000Z/\nSuites: forky\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n';Path('/etc/apt/sources.list.d/pinned.sources').write_text(stanzas)
   apt=['/usr/bin/apt-get','-o','Acquire::Retries=0','-o','APT::Update::Error-Mode=any'];command([*apt,'update'],timeout=600)
   for name,pin in RELEASE_PINS.items():Loan(s,Path('/var/lib/apt/lists')/name,pin,maximum=1024**2)
   packages=[n+'='+v for n,v in deps if n.split(':')[0]not in{'libacl1','libacl1-dev'}];command([*apt,'-y','--no-install-recommends','install',*packages],timeout=1200)
   command([*apt,'-y','--no-install-recommends','build-dep','--build-profiles=noudeb','--only-source','coreutils=9.7-3'],timeout=600)
   command([*apt,'-y','--no-install-recommends','install','build-essential','dpkg-dev','binutils','ca-certificates','pkgconf','patch','debian-keyring','gpgv','util-linux','passwd','autoconf','automake','autopoint','dh-autoreconf','fakeroot','locales-all','acl','attr'],timeout=600)
   command(['/usr/sbin/groupadd','--gid','10001','candidate-builder'],timeout=30);command(['/usr/sbin/useradd','--uid','10001','--gid','10001','--home-dir','/work/home','--no-create-home','--shell','/usr/sbin/nologin','candidate-builder'],timeout=30)
   s.call(check_dependencies,S,deps);Path('/work').mkdir(mode=0o755);os.chown('/work',10001,10001);Path('/work/home').mkdir(mode=0o700);os.chown('/work/home',10001,10001)
   dest=Path('/build/source-inputs');dest.mkdir(mode=0o755);command([*apt,'source','--download-only','--only-source','coreutils=9.7-3'],cwd=dest,timeout=600);need({p.name for p in dest.iterdir()}==set(SOURCE_PRODUCTS),'source_products')
   for name,pin in SOURCE_PRODUCTS.items():Loan(s,dest/name,tuple(pin),maximum=8*1024**2)
   signature=s.call(S.source_signature,dest/'coreutils_9.7-3.dsc');need(signature['signer_fingerprint']=='02D5315FF11F1861860E1E02F61AACDCFA11FFDE','DSC_signer');report={'status':'PREPARED','source_signature':signature,'no_SDK_or_package_build':True}
  else:
   need(sorted(os.listdir('/sys/class/net'))==['lo'],'offline_only')
   for name,pin in SOURCE_PRODUCTS.items():Loan(s,Path('/build/source-inputs')/name,tuple(pin),maximum=8*1024**2)
   for path,raw in hdr.items():
    p=Path(path);need(p.parent.resolve(strict=True)==p.parent and p.resolve(strict=True)==p and p.lstat().st_uid==0 and stat.S_ISREG(p.lstat().st_mode),'header_parent');p.unlink();fixed_write(s,p,raw)
   # No SDK package identity is manufactured. Install only approved runtime,
   # and fix the ordinary linker symlink in this disposable environment.
   Loan(s,Path('/inputs/libacl1_2.4.0-1~trixie+gold1_amd64.deb'),RUNTIME_PIN,modes=(0o600,));s.call(S.install_packages,[Path('/inputs/libacl1_2.4.0-1~trixie+gold1_amd64.deb')],documentation=('libacl1',))
   lib=Path('/usr/lib/x86_64-linux-gnu/libacl.so.1').resolve(strict=True);Loan(s,lib,LIB_PIN);link=lib.parent/'libacl.so';link.unlink(missing_ok=True);link.symlink_to(lib.name);need(link.resolve(strict=True)==lib,'normal_linker_target');NormalLink(s,link,lib)
   for path,raw in hdr.items():Loan(s,Path(path),(len(raw),sha256(raw).hexdigest()))
   S.WORK.mkdir(mode=0o755);public=S.WORK/'public-inputs';public.mkdir(mode=0o755)
   for name,pin in SOURCE_PRODUCTS.items():S.copy_exact(Path('/build/source-inputs')/name,public/name,tuple(pin),mode=0o644)
   for name in S.RECIPE_FILES:
    if name!='Dockerfile':pin=S.digest(Path('/recipe')/name);S.copy_exact(Path('/recipe')/name,public/name,(pin['bytes'],pin['sha256']),mode=0o644)
   root=S.WORK/'coreutils';root.mkdir(mode=0o755);os.chown(root,10001,10001);source=root/'source';command(['/usr/bin/dpkg-source','-x',str(public/'coreutils_9.7-3.dsc'),str(source)],cwd=root,user=10001,timeout=120)
   manifest=json.loads(S.bytes_at(public/'source-manifest.json',S.SOURCE_MANIFEST_PIN));S.source_contract(source,manifest,'before')
   need(S.observed(source/'m4/acl.m4',maximum=S.LINK_M4_BEFORE[0])=={'bytes':S.LINK_M4_BEFORE[0],'sha256':S.LINK_M4_BEFORE[1]},'qcopy_m4_original_pin')
   for name,pin in((S.PATCH,S.PATCH_PIN),(S.CHANGELOG,S.CHANGELOG_PIN),(S.REGEN_PATCH,S.REGEN_PIN),(S.PROBE_PATCH,S.PROBE_PIN),(S.LINK_PATCH,S.LINK_PIN),(S.PARENT_PATCH,S.PARENT_PIN)):
    S.digest(public/name,pin);command(['/usr/bin/patch','-p1','--batch','--fuzz=0','--no-backup-if-mismatch','-i',str(public/name)],cwd=source,user=10001,timeout=30)
   need(S.observed(source/'m4/acl.m4',maximum=S.LINK_M4_AFTER[0])=={'bytes':S.LINK_M4_AFTER[0],'sha256':S.LINK_M4_AFTER[1]},'qcopy_m4_declared_patch_pin')
   S.source_contract(source,manifest,'after');regenerated_before=S.regeneration_inputs(source)
   need(all(regenerated_before[n]==pin for n,pin in manifest['regeneration_infrastructure']['original_m4_pins'].items()),'original_imported_m4_pin')
   changelog=source/'debian/changelog';original=S.owned_read(changelog);prefix=('coreutils ('+S.VERSION+') trixie; urgency=medium\n\n  * Local isolated security vendor build; private Coreutils caller fix only.\n  * No libattr ABI or separately approved ACL runtime promotion.\n\n -- Project local build <local-build@invalid>  Tue, 27 Oct 2026 00:00:00 +0000\n\n').encode();changelog.write_bytes(prefix+original)
   for argv in configure_commands():command(argv,cwd=source,user=10001,timeout=900)
   S.uid_idle();need(S.regeneration_inputs(source)==regenerated_before,'m4_po_unchanged');S.source_contract(source,manifest,'after')
   # Only the unchanged configure targets may compile their ordinary conftests.
   # Exact header/library/link inputs are retained, but those input bindings do
   # not themselves prove the dynamic provider selected by a configure probe.
   # Export the real config.log observations for a separate semantic decision.
   S.OUT.mkdir(mode=0o755);rows={};total=0
   for name,leaf in GENERATED.items():
    raw=S.owned_read(source/name,maximum=BODY_MAX);total+=len(raw);need(total+RECEIPT_MAX<=OUTPUT_MAX,'total_output');rows[name]={'actual':fixed_write(s,S.OUT/leaf,raw),'historical':HISTORICAL[name],'matches_historical':(len(raw),sha256(raw).hexdigest())==tuple(HISTORICAL[name])}
   matched=rows['lib/config.h']['matches_historical'];versions=metadata(['/usr/bin/dpkg-query','-W','-f=${Package}\t${Version}\n']).decode();report={'schema':'coreutils_exact_configure_only_recovery/1','controller_sha256':args.controller_sha256,'source_products':SOURCE_PRODUCTS,'legacy_source_pin':SOURCE_PIN,'legacy_host_pin':HOST_PIN,'actual_kernel':os.uname().release,'signed_snapshot_release_pins':RELEASE_PINS,'SDK_header_pins':{p:{'bytes':v['bytes'],'sha256':v['sha256']}for p,v in SDK_HEADERS.items()},'status':'EXACT_HEADER_RECOVERED'if matched else'CONFIGURATION_DIFFERENT_NOT_RECOVERED','header_exact':matched,'generated':rows,'shared_provider_input_binding':{'path':str(lib),'bytes':LIB_PIN[0],'sha256':LIB_PIN[1],'ordinary_link_path':str(link),'ordinary_link_target':os.readlink(link)},'dynamic_provider_proof':'OPEN_NOT_ESTABLISHED_BY_THIS_RECOVERY','ordinary_configure_probe_observation':rows['config.log'],'actual_dpkg_versions':versions,'actual_configure_commands':configure_commands(),'ordinary_configure_probes_only':True,'actual_extra_provider_link_probe':False,'SDK_package_identity_recovered':False,'config_log_is_fresh_observation':True,'no_full_build_vendor_or_SDK_fallback':True,'security_accepted':False};fixed_write(s,S.OUT/'configure-recovery.json',encode(report))
 except BaseException as e:s.note(e)
 finish(s);return report

def execute(args):
 s=State();H=None;cid=None;workspace=None;workspace_binding=None;result=None;nonce=None;offline=False;env=None;stopped=False
 try:
  need(args.confirm==CONFIRM,'explicit_recovery_confirmation');repo=Path(args.repo).absolute();own=repo/'deploy/maintenance/coreutils_acl_build/configure_recovery.py';need(Path(__file__).absolute()==own,'controller_path');Loan(s,own,(own.stat().st_size,args.controller_sha256),owners=(1001,));H=load(s,own.with_name('build_hosted.py'),HOST_PIN);load(s,own.with_name('sourcebuild.py'),SOURCE_PIN)
  oldargs=SimpleNamespace(repo=str(repo),root=args.root,commit=args.commit,source_sha256=HOST_PIN[1],confirm=H.CONFIRM);repo,root,run_id,attempt=H.hosted_layout(oldargs,os.environ);H.host_facts(root.parent);root.mkdir(mode=0o700);context=root/'context';context.mkdir(mode=0o700);(context/'inputs').mkdir(mode=0o700);logs=root/'reports';logs.mkdir(mode=0o700);output=root/'configure-output';output.mkdir(mode=0o700)
  env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(root),'LC_ALL':'C.UTF-8','DOCKER_CONFIG':str(root/'docker-config'),'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'};Path(env['DOCKER_CONFIG']).mkdir(mode=0o700)
  def command(argv,maximum=65536,seconds=60,child_env=None):return H.run(argv,env=env if child_env is None else child_env,deadline=min(s.end,time.monotonic()+seconds),maximum=maximum,guard=s.check)
  for name,pin in H.PINS.items():
   if name.startswith('inputs/'):continue
   src=own.parent/name;loan=Loan(s,src,pin,owners=(1001,));need(command(['/usr/bin/git','-C',str(repo),'cat-file','blob',args.commit+':'+str(H.TRACKED/name)],maximum=pin[0]+1)==loan.read(),'tracked_recipe');fixed_write(s,context/name,loan.read())
  token=os.environ.get('GH_TOKEN');need(type(token)is str and token,'public_artifact_authentication');fetch={**env,'GH_TOKEN':token,'GH_HOST':'github.com','GH_PROMPT_DISABLED':'1','GH_NO_UPDATE_NOTIFIER':'1'};metadata=json.loads(command(['/usr/bin/gh','api','repos/'+H.REPOSITORY+'/actions/artifacts/'+str(H.OLD_ARTIFACT_ID)],child_env=fetch));need(metadata.get('id')==H.OLD_ARTIFACT_ID and metadata.get('expired')is False and metadata.get('size_in_bytes')==H.OLD_ZIP_PIN[0]and metadata.get('workflow_run',{}).get('id')==H.OLD_RUN_ID,'artifact_metadata');archive=command(['/usr/bin/gh','api','repos/'+H.REPOSITORY+'/actions/artifacts/'+str(H.OLD_ARTIFACT_ID)+'/zip'],maximum=H.OLD_ZIP_PIN[0]+1,seconds=300,child_env=fetch);del token,fetch;need((len(archive),sha256(archive).hexdigest())==H.OLD_ZIP_PIN,'artifact_pin')
  legacy_loans=[];H.extract_runtime_inputs(archive,context,legacy_loans)
  for r in legacy_loans:
   Loan(s,r.path,H.OLD_ZIP_PIN if False else RUNTIME_PIN,owners=(1001,),modes=(0o600,));r.close()
  fixed_write(s,context/'context-manifest.json',H.encoded({'schema':'coreutils_acl_allowlisted_context/1','files':{n:{'bytes':v[0],'sha256':v[1]}for n,v in H.PINS.items()}}),0o600);allow='**\n!.dockerignore\n!context-manifest.json\n'+''.join('!'+n+'\n'for n in H.PINS if not n.startswith('inputs/'))+'!inputs/\n'+''.join('!'+n+'\n'for n in H.PINS if n.startswith('inputs/'));fixed_write(s,context/'.dockerignore',allow.encode(),0o600);H.context_inventory(context)
  workspace=H.create_native_workspace(root,command);workspace_binding=(workspace,workspace.fd,flags(workspace.fd),workspace.before);s.workspace=workspace;nonce=os.urandom(16).hex()
  def guard():
   s.check();need((workspace,workspace.fd,flags(workspace.fd),workspace.before)==workspace_binding,'workspace_actual_fd_alias');workspace.check()
  def cli(argv,maximum=65536,seconds=60):guard();value=command([H.DOCKER,'--host',H.HOST,*argv],maximum,seconds);guard();return value
  def inspect():
   value=json.loads(cli(['container','inspect','--format',H.INSPECT,cid]));H.validate_inspect(value,nonce,cid,context,workspace.path,offline=offline,running=value['running'],paused=value['paused']);return value
  cid=command(H.create_argv(nonce,context,workspace.path),maximum=128).decode().strip();need(re.fullmatch('[a-f0-9]{64}',cid),'container_id');cli(['container','start',cid]);inspect();copies=json.loads(cli(['container','exec',cid,'/usr/local/bin/python3','-I','-S','-B','-c',H.stage_code(context,False)]));H.validate_copies(copies);cli(['container','cp',str(own),cid+':/build/recovery-controller.py'])
  for phase in('prepare','configure'):
   if phase=='configure':cli(['network','disconnect','bridge',cid]);offline=True;inspect()
   raw=cli(['container','exec','--workdir','/build',cid,'/usr/local/bin/python3','-I','-S','-B','/build/recovery-controller.py','--inner-phase',phase,'--controller-sha256',args.controller_sha256],maximum=16*1024**2,seconds=1500);fixed_write(s,logs/(phase+'.log'),raw,0o600,maximum=OUTPUT_MAX)
  copies=json.loads(cli(['container','exec',cid,'/usr/local/bin/python3','-I','-S','-B','-c',H.stage_code(context,True)]));H.validate_copies(copies)
  script=manifest_script(H);before=json.loads(cli(['container','exec',cid,'/usr/local/bin/python3','-I','-S','-B','-c',script]));need(set(before)==set(GENERATED.values())|{'configure-recovery.json'}and sum(r['bytes']for r in before.values())<=OUTPUT_MAX,'fixed_output_snapshot');cli(['container','pause',cid]);need(inspect()['paused']is True,'export_pause');receipt=None
  for leaf in(*GENERATED.values(),'configure-recovery.json'):
   cli(['container','cp',cid+':/out/'+leaf,str(output/leaf)]);r=before[leaf];row=Loan(s,output/leaf,(r['bytes'],r['sha256']),owners=(1001,),maximum=BODY_MAX if leaf!='configure-recovery.json'else RECEIPT_MAX)
   if leaf=='configure-recovery.json':receipt=json.loads(row.read())
  cli(['container','unpause',cid]);need(inspect()['paused']is False,'export_unpause');after=json.loads(cli(['container','exec',cid,'/usr/local/bin/python3','-I','-S','-B','-c',script]));need(after==before,'producer_output_changed');need(type(receipt.get('header_exact'))is bool,'exported_header_gate');inspect();cli(['container','stop','--time','10',cid],seconds=30);need(inspect()['running']is False,'builder_stopped');stopped=True;result={'status':'CONFIGURE_ONLY_OUTPUTS_NOT_SECURITY_ACCEPTED','container_id':cid,'output':str(output),'no_SDK_or_package_or_vendor_build':True,'commit':args.commit,'controller_sha256':args.controller_sha256,'header_exact':receipt['header_exact']}
 except BaseException as e:
  s.note(e);s.failure_object=e
  if cid is not None:
   try:
    value=json.loads(H.run([H.DOCKER,'--host',H.HOST,'container','inspect','--format',H.INSPECT,cid],env=env,deadline=min(s.end,time.monotonic()+30),maximum=65536,guard=s.check));H.validate_inspect(value,nonce,cid,context,workspace.path,offline=offline,running=value['running'],paused=value['paused']);
    if value['paused']:H.run([H.DOCKER,'--host',H.HOST,'container','unpause',cid],env=env,deadline=min(s.end,time.monotonic()+30),maximum=65536,guard=s.check)
    H.run([H.DOCKER,'--host',H.HOST,'container','stop','--time','10',cid],env=env,deadline=min(s.end,time.monotonic()+30),maximum=65536,guard=s.check);after=json.loads(H.run([H.DOCKER,'--host',H.HOST,'container','inspect','--format',H.INSPECT,cid],env=env,deadline=min(s.end,time.monotonic()+30),maximum=65536,guard=s.check));H.validate_inspect(after,nonce,cid,context,workspace.path,offline=offline,running=False,paused=False);stopped=True
   except BaseException as cleanup:s.note(cleanup)
 finally:
  if workspace is not None and(cid is None or stopped):
   try:
    need((workspace,workspace.fd,flags(workspace.fd),workspace.before)==workspace_binding,'workspace_close_alias');original_fd=workspace_binding[1];workspace.close()
    try:os.fstat(original_fd)
    except OSError as e:need(e.errno==errno.EBADF,'workspace_physical_close')
    else:raise RuntimeError('workspace_unknown_close')
   except BaseException as e:s.note(e)
 s.container_id=cid;s.container_stopped=stopped
 if s.errors and 'logs'in locals():
  public=State()
  try:
   captured=getattr(getattr(s,'failure_object',None),'public_command_failure',{}).get('captured_output',b'')
   if captured:fixed_write(public,logs/'failed-command.log',captured,0o600,maximum=OUTPUT_MAX)
   fixed_write(public,logs/'failure.json',encode({'schema':'configure_only_failed_scope_observation/1','error_classes':[type(e).__name__ for e in s.errors],'container_id':cid,'container_stopped_and_verified':stopped,'workspace_held':workspace is not None and not stopped,'original_scope_restored_or_retried':False,'security_accepted':False}),0o600);finish(public)
  except BaseException as publishing:s.note(publishing)
 finish(s);return result

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute-configure-only',action='store_true');parser.add_argument('--confirm');parser.add_argument('--repo');parser.add_argument('--root');parser.add_argument('--commit');parser.add_argument('--controller-sha256');parser.add_argument('--inner-phase',choices=('prepare','configure'));args=parser.parse_args(argv)
 if args.inner_phase:print(encode(inner(args)).decode(),end='');return 0
 if not args.execute_configure_only:print(json.dumps({'status':'UNARMED','native_performed':False,'security_accepted':False}));return 0
 need(args.controller_sha256 is not None and re.fullmatch('[a-f0-9]{64}',args.controller_sha256),'controller_pin');result=execute(args);print(json.dumps(result,sort_keys=True));return 0 if result['header_exact'] else 1
if __name__=='__main__':raise SystemExit(main())
