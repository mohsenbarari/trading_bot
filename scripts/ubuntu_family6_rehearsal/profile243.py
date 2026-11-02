"""Fixed exact Ubuntu 2.43-2ubuntu2.4 installed-family6 data; local candidate only."""
HOST = {'base': 'ubuntu:26.04@sha256:157dc3d6e4a3e4f2c68d12df9df9ed6e808b59af6d4c3757b432b4f519916725',
 'bootstrap_prepare': ['/bin/sh',
 '-c',
 'set -eu\n'
 'export DEBIAN_FRONTEND=noninteractive\n'
 '# The reviewed public ISRG root is in the existing read-only recipe context.\n'
 '# This bootstraps normal CA packages without disabling TLS or APT signatures.\n'
 'test -f /public-context/snapshot-public-isrg-root-x1.crt\n'
 'test ! -L /public-context/snapshot-public-isrg-root-x1.crt\n'
 "printf '%s  %s\\n' '22b557a27055b33606b6559f37703928d3e4ad79f110b407d04986e1843543d1' "
 "'/public-context/snapshot-public-isrg-root-x1.crt' | sha256sum -c -\n"
 'test ! -L /etc/apt/sources.list\n'
 'for snapshot_source in /etc/apt/sources.list.d/*; do\n'
 ' [ -e "$snapshot_source" ] || [ -L "$snapshot_source" ] || continue\n'
 ' test -f "$snapshot_source"; test ! -L "$snapshot_source"\n'
 ' rm -- "$snapshot_source"\n'
 'done\n'
 ': > /etc/apt/sources.list\n'
 "cat > /etc/apt/sources.list.d/locked-snapshot-bootstrap.sources <<'SNAPSHOT_BOOTSTRAP_SOURCES'\n"
 'Types: deb\n'
 'URIs: https://snapshot.ubuntu.com/ubuntu/20261101T000000Z/\n'
 'Suites: resolute resolute-updates resolute-security\n'
 'Components: main universe\n'
 'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n'
 'SNAPSHOT_BOOTSTRAP_SOURCES\n'
 'apt-get -o Acquire::Retries=0 -o APT::Update::Error-Mode=any -o '
 'Acquire::AllowInsecureRepositories=false -o APT::Get::AllowUnauthenticated=false -o '
 'Acquire::https::CaInfo=/public-context/snapshot-public-isrg-root-x1.crt update\n'
 'apt-get -o Acquire::Retries=0 -o Acquire::AllowInsecureRepositories=false -o '
 'APT::Get::AllowUnauthenticated=false -o '
 'Acquire::https::CaInfo=/public-context/snapshot-public-isrg-root-x1.crt -y '
 '--no-install-recommends install python3 ca-certificates ubuntu-keyring\n'],
 'confirm': 'BUILD-ONLY-DISPOSABLE-CI-TWO-UBUNTU243-GLIBC-84243-FULL-PACKAGES-20261101-01',
 'idle_argv': ['/usr/bin/sleep', '19800'],
 'name': 'ubuntu243',
 'package_version': '2.43-2ubuntu2.4+gold1',
 'python': '/usr/bin/python3',
 'root_prefix': 'glibc84243-two-ubuntu243',
 'runtime_packages': ['libc6',
                      'libc-bin',
                      'libc-dev-bin',
                      'libc-gconv-modules-extra',
                      'libc6-dev',
                      'locales']}

WORKER = {'after': {'intl/Makefile': (6024, 'f43a1c91aaf1a3a22e359415eb5011df7141f1852bc46649e23dfac8bed0e09f'),
           'intl/dcigettext.c': (46515, 'f2ab262a35ca2207f16241f99c11ac324b13bba987227114886a06a046fb62f3'),
           'intl/tst-gettext-path-traversal.c': (3336,
                                                 'c732e301bbe037a6aa612f1452d7768d0bb3efd893f4fbf1d4d02e4651e2d3f6')},
 'apt_sources': ('Types: deb deb-src\n'
 'URIs: https://snapshot.ubuntu.com/ubuntu/20261101T000000Z/\n'
 'Suites: resolute resolute-updates\n'
 'Components: main universe\n'
 'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n'
 '\n'
 'Types: deb deb-src\n'
 'URIs: https://snapshot.ubuntu.com/ubuntu/20261101T000000Z/\n'
 'Suites: resolute-security\n'
 'Components: main universe\n'
 'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n'),
 'baseline_debs': {'libc-bin_2.43-2ubuntu2.4_amd64.deb': (700954,
                                                          'bc4f50a0aebbb348598749c291a3742f9485d94b044715d382388607b9159a1b'),
                   'libc-dev-bin_2.43-2ubuntu2.4_amd64.deb': (23328,
                                                              '515a2f156a94b7467842409f9bd9db1330b136f44a1791ed747f0708fb1a7227'),
                   'libc-gconv-modules-extra_2.43-2ubuntu2.4_amd64.deb': (1362202,
                                                                          'c2194b87c7f628722036bbe9f9bbdf20c1ebdb05ecd68b7a3a52dd1a76f3c5d6'),
                   'libc6-dev_2.43-2ubuntu2.4_amd64.deb': (2294116,
                                                           '17bb816a65a25c3416854eef7e9cc6d9a3761f45bad0d44e49bc676b7bab1769'),
                   'libc6_2.43-2ubuntu2.4_amd64.deb': (2104056,
                                                       'a613b457f3ff9c84ebd28af8a05afdec22605ad9aed87c71e3a6ae5170aeb9ca'),
                   'locales_2.43-2ubuntu2.4_all.deb': (4225234,
                                                       'a1abf41b517f767018d5eab519d55b9269fe0a04ae49ea35f7c3ad1b8870ab32')},
 'before': {'intl/Makefile': (5815, '03ab7b8f574070746a1e5096f5e4f33eb8665d956ded3577840e1a119e9a3332'),
            'intl/dcigettext.c': (46551, '60e20644b6acfd8f9f48dd40e6d15be38ecc546e43028c27cdb3ddcfc59f4859')},
 'dsc_signer': None,
 'extra_tools': [],
 'keyring_package': 'ubuntu-keyring',
 'name': 'ubuntu243',
 'old_libc': (2190608, '85e64f97e348786a8fb4d9f3d52fec289e2fb86bba20f0731dfe61990525e0f7'),
 'old_loader': (254864, 'bda779ea9e9ad234f60315477d7cfd0bdde861cd8ddd9df8cdaf62f18c4cec14'),
 'old_version': '2.43-2ubuntu2.4',
 'os_codename': 'resolute',
 'os_id': 'ubuntu',
 'patch': 'CVE-2026-84243-Ubuntu-Web-2.43-2ubuntu2.4-CANDIDATE.patch',
 'runtime_packages': ['libc6',
                      'libc-bin',
                      'libc-dev-bin',
                      'libc-gconv-modules-extra',
                      'libc6-dev',
                      'locales'],
 'source_files': {'glibc_2.43-2ubuntu2.4.debian.tar.xz': (530764,
                                                          '28103a7cf808c29901c6053d89a4e8299880abfbb850c0d97a09c8df8533e306'),
                  'glibc_2.43-2ubuntu2.4.dsc': (9078,
                                                'da10d551ca51076bf7bf663de38369d04e267386b8211c283c2814e68e482ed3'),
                  'glibc_2.43.orig.tar.xz': (20297012,
                                             'd9c86c6b5dbddb43a3e08270c5844fc5177d19442cf5b8df4be7c07cd5fa3831')},
 'target_dist': 'resolute',
 'version': '2.43-2ubuntu2.4+gold1'}
