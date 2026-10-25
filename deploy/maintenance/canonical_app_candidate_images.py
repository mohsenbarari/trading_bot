#!/usr/bin/env python3
"""One actual canonical App image, stores only; no serving or data transition."""
from contextlib import ExitStack,contextmanager
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,io,json,os,re,select,stat,time,zipfile

OPERATION='security-canonical-app-candidate-images-20261025-01'
CONFIRM='CANONICAL-APP-IMAGE-STORES-ONLY-20261025-01'
WORKER='canonical_app_candidate_images.py'
ROOTS={'bot':Path('/root/trading-bot/trading_bot/.local')/OPERATION,'web':Path('/srv/trading-bot')/OPERATION}
ENGINE_SHA='1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e'
CATALOG_SHA='bebdd67daf4ef7950b2c4278cd338ec0d11130ea84df0a7fd8c41d4a9ddc13d0'
PHASES=('admit-existing-web','load-web','load-native')
EXISTING_TAR=Path('/srv/trading-bot/security-canonical-app-665220a2-20261025-01/source.tar')
SOURCE_ID=[2064,1835555,33152,0,0,1,0,1787336928392178187,1788695466612272194]
PUBLIC_CODES=frozenset(('app_catalog','app_ci_gate','app_evidence','app_archive','app_admission','app_admission_shape','catalog_binding','baseline_scope','app_peer_preheader_refused'))
HELPERS={'deploy/maintenance/datastore_candidate_contract.py': {'bytes': 38382, 'sha256': 'd210020e24fedd4986803a3fe2618a5016ecd99f9e3ca9588af02d77a967e17d'}, 'deploy/maintenance/runtime_image_archive_transport.py': {'bytes': 35148, 'sha256': '20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2'}, 'scripts/admit_security_component_image_reuse.py': {'bytes': 27518, 'sha256': '9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025'}, 'scripts/compose_market_security_os_candidate.py': {'bytes': 52231, 'sha256': 'c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9'}, 'scripts/run_canonical_app_ci_smoke.py': {'bytes': 35767, 'sha256': '265d272878112c69fde7fb3e6bd0ae82b41fee2a0ea454c24e56f43a2ed7ad09'}}

CATALOG_SOURCE=b'{"actual_ci":{"artifact_id":12599936628,"bytes":392398,"controller_commit":"200f5c718a7bad0ce0860f37ada66a7baa05a887","members":["evidence/public-evidence.zip","reports/actual-smoke.json","reports/app-mount-admission.json","reports/authenticated-export.json","reports/canonical-archive-admission.json","reports/canonical-daemon-admission.json","reports/canonical-source-binding.json","reports/cleanup.json","reports/fresh-verify_acl_consumer_presence.json","reports/fresh-verify_image_component_presence.json","reports/postgres-admission.json","reports/redis-admission.json","reports/source-identity.json"],"record_sha256":{"reports/actual-smoke.json":"d9f730ea31f91db251a6734e384719fc2b9e99183ce7e4b60e9b63df680693cb","reports/canonical-archive-admission.json":"19be8ba43f40763af628a3c68ddabaab8cbfed4c62256d8bd13651ddc29738db","reports/canonical-daemon-admission.json":"1c39a924689ee061d7c3d75f4f335fac549a7f6bcf1bf3bb7d7e2e2907543441","reports/canonical-source-binding.json":"3a987920c791767d705947606fd5449e69fdc7aed1c85c73413970c0323fe4a7","reports/cleanup.json":"a0ad3cc662ef09c3a5aeb6c7c21e3a178a7dd00ee49835869dafbe2fd81c1089","reports/source-identity.json":"4074a67c49ba047c8b8c344c2e0ff1fa74df6a7f4b67bf984d552ef22d606f20"},"records":{"reports/actual-smoke.json":{"app_source_masked":false,"component_scope":"6_parser_and_18_Redis_regressions_with_existing_component_test_doubles","components":{"errors":0,"expected":24,"failures":[],"passed":24,"run":24,"skipped":0},"full_matrix_accepted":false,"image_config_id":"sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1","migration":{"child_completed":true,"invocation_attempted":true,"return_code":0},"migration_head":"ff6c7d8e9f01","not_tested":["browser","Bot_delivery","sync","backup_restore","production_network_TLS","background_scheduler_continuity"],"production_config_guards_active":true,"real_provider_used":false,"runtime":{"expected":8,"failures":[],"passed":8,"run":8},"runtime_cleanup_failures":[],"runtime_scope":"installed_API_lifespan_auth_OTP_upload_album_A_D","schema":"app_image_ci_smoke/1","security_accepted":false,"status":"passed"},"reports/canonical-archive-admission.json":{"archive_bytes":452719104,"archive_sha256":"e10090370762baf289dd55457f648b5c78b05e8e398c724397336b1d89006c67","config_id":"sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1","configuration_values_disclosed":false,"daemon_identity_measured":false,"expanded_layer_bytes":452635136,"index_sha256":"fa9481689c7165671348103769538e9e17b522f53b3ae51d5b38bbd3aedc022f","legacy_compatibility_configs":33,"manifest_id":"sha256:2f609ac53add114037016e54482486cfb455e41b1d79c6625c2772b07bb81908","member_count":71,"physical_graph_closed":true,"profile":"canonical_app","repo_tags_empty":true,"rootfs_layers":["sha256:a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7","sha256:535a5d38abecfac4fa49da33fa942b53c53c463bd0d39a860967e46eb03faafe","sha256:6972161a9a77e7c1afb997daac15abc54f5b70c7b40b814601f53e038d8c398c","sha256:d86e9bd004831a6069b9a02e5caac80b73526315f52719079b4017b099956ed2","sha256:3e204cd21b2edfd2cf9d77a1d2032725307c8f01043e2eca16f35e801742f66b","sha256:8de59326698e4655f63fd4e5532c474f3abafda7e32edded25050d5f9eea5820","sha256:a842894c2b3256a955801a4f3d634ade21c3a93f5f9b5aaf58193576d16655e0","sha256:c9da23d02bc39c438741e196f1950f1a30107f71bb7192c6af91450ea696f764","sha256:d31cd1c8922519c5e60c0396430645de0b71973b82136600b382783d11c0b0c8","sha256:457975dd4444c78af6aac81f1d4b44585052c94aa52a5d942acbdb8561154142","sha256:8183b6021d6775b488b267abaa469f2dc86e7b73b0a5dd5d3daef9172d0e8074","sha256:ab4640e9ff51275ad55673a6ba12dd1c7f61512e5d3a5f0c524c7ecbf8130467","sha256:f66767c7dd83565dcccbbaa6d524faa79a0aa4cee3de2978e2c77e9ac0799b26","sha256:9539b4fb7d1f332da6d1d041718d0ea6c0312f272fe2b37b0121274363ea35d9","sha256:d365532c6abd0b186bf0b5b510bb0dffe2ca8c912e6ef7f15b19dcb5d39bad20","sha256:aba3d55fbc0cd22b6762952e92c7a46892ca2d889c07665719c7fe04225479cf","sha256:ab7840770e7d2a526cb62900bc9e5dcdfa2454dbf6c6531c13da84e70544bf4d","sha256:093ccb908184c22066df90e52fc388e3f26f6cd8ec435b9a7b7be2f0cb9ec0d1","sha256:29b52a15e9e6677ffad8799b8eec656f250035ea1a7403c336cd652aad6cdc8b","sha256:b06c2c3dd56d631de5deda8744e78b95360c6f31edabfef65643d78f709a0ffc","sha256:9f19d62d62ec6c2b527d73594454648dbd772bc40114fdcf019ed9605b5085bb","sha256:a4cd6d8ce7054acdf3fea36cbc0ee6243337b50b5ef7502727cba8c4bd9dc329","sha256:d29d88236a8b8f0f46a61014304fdeb828ab7a9802c6105048a32f2b1760cede","sha256:db5de29cb9291c166a771b93fd64dc577f1e5de6e935e7178bf645becd7ae175","sha256:00aff1708af51ca851723d52ee94b49c13bf2596c6948ceb344ba51d1fe89f8c","sha256:2014b2e5edc6c1820bc23801c6f6020fdb0b53abe1170d7edbe3ac24d5393b3c","sha256:b8edb16e8cc1f34718d93054e59e40fafcf727807affaecffc9d10309d2d7108","sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef","sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef","sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef","sha256:d11e8eb630dda8fb072612381737f8e056c58bd8a412d3274c7694d55239e7a5","sha256:4c632541a034234eb926259de0f177216f0bbc41516e730a9e7b7d38e0106200","sha256:b05a2827ff6e9540948e7eda99ae0410a3364373a4cfb5285ac9422c4f0d8180"],"schema":"datastore_candidate_archive_admission/1","security_accepted":false,"source_identities":{"sha256:2f609ac53add114037016e54482486cfb455e41b1d79c6625c2772b07bb81908":{"kind":"OCI_manifest_to_config","media_type":"application/vnd.oci.image.manifest.v1+json","size":5254},"sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1":{"kind":"Docker_config_identity"},"sha256:fa9481689c7165671348103769538e9e17b522f53b3ae51d5b38bbd3aedc022f":{"kind":"OCI_index_to_manifest_to_config","media_type":"application/vnd.oci.image.index.v1+json","size":241}}},"reports/canonical-daemon-admission.json":{"configuration_values_disclosed":false,"daemon_image_id":"sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1","image_config_id":"sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1","rootfs_layers":33,"rootfs_sha256":"38e656d28162262f8a0e6c565463f0752316b966273ee4709885f3561ba80a61","security_accepted":false,"whole_config_compared_in_ram":true},"reports/canonical-source-binding.json":{"build_run_attempt":1,"build_run_id":40191605131,"controller_commit":"200f5c718a7bad0ce0860f37ada66a7baa05a887","evidence_artifact_id":12598516295,"evidence_zip_sha256":"eac66d11595ae9bd7a4478f5b126ac77e4d36b81dab25613f0d951c833d7b653","full_matrix_accepted":false,"image_id":"sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1","image_source_commit":"665220a2b5086597daa4565eef6450a37aaf47f4","image_source_tree":"f522d42f754599bb9ebd0ea0e284a338ec1dcfbf","input_signature":"a63abda5a1b5fd889575be09d0cdff0412ad0c0cd062f85336fedf0237cf87a2","raw_counts":{"CRITICAL":0,"HIGH":44,"LOW":82,"MEDIUM":68,"UNKNOWN":1},"security_accepted":false},"reports/cleanup.json":{"closed_causes":[],"owned_cleanup_complete":true,"primary_failure_retained":false,"security_accepted":false},"reports/source-identity.json":{"controller_commit":"200f5c718a7bad0ce0860f37ada66a7baa05a887","files":{"admit_security_component_image_reuse.py":{"bytes":27518,"path":"scripts/admit_security_component_image_reuse.py","sha256":"9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025"},"compose_app_mountpoint_image.py":{"bytes":49042,"path":"scripts/compose_app_mountpoint_image.py","sha256":"14849bd8c616b27ade1c8488b8e6f276b369480456d0acc1a8c8eb4284f40226"},"compose_market_security_os_candidate.py":{"bytes":52231,"path":"scripts/compose_market_security_os_candidate.py","sha256":"c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9"},"compose_security_os_candidate.py":{"bytes":54267,"path":"scripts/compose_security_os_candidate.py","sha256":"10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44"},"datastore_candidate_contract.py":{"bytes":38382,"path":"deploy/maintenance/datastore_candidate_contract.py","sha256":"d210020e24fedd4986803a3fe2618a5016ecd99f9e3ca9588af02d77a967e17d"},"prepare_canonical_app_image.py":{"bytes":27676,"path":"scripts/prepare_canonical_app_image.py","sha256":"e62ca64c27f81f5de79e096c9acf18da135c6644641607e1b1e6d44373b415fc"},"run_app_image_ci_smoke.py":{"bytes":68163,"path":"scripts/run_app_image_ci_smoke.py","sha256":"09221619f6929df66f2622de8911913e8bf3770c96001ae5e06d77a9851abb62"},"run_canonical_app_ci_smoke.py":{"bytes":35767,"path":"scripts/run_canonical_app_ci_smoke.py","sha256":"265d272878112c69fde7fb3e6bd0ae82b41fee2a0ea454c24e56f43a2ed7ad09"},"runtime_image_archive_transport.py":{"bytes":35148,"path":"deploy/maintenance/runtime_image_archive_transport.py","sha256":"20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2"},"test_security_form_parser.py":{"bytes":3910,"path":"tests/test_security_form_parser.py","sha256":"de0090351837742ba6b0be747f0b586d4943590831215a4b4ea2619ebd5149fb"},"test_stage6_otp_delivery_redis.py":{"bytes":25543,"path":"tests/test_stage6_otp_delivery_redis.py","sha256":"b1e59ad7285a3c1f034aa269e024e091fe331cbdb446ac5e82f85de64530bd50"},"test_telegram_otp_ephemeral_queue_redis.py":{"bytes":8024,"path":"tests/test_telegram_otp_ephemeral_queue_redis.py","sha256":"cbe02df0618239e4affa1f8e51b2e69cdfe13f332a44d46e8b4626b21d80e22e"},"verify_acl_consumer_presence.py":{"bytes":13746,"path":"scripts/verify_acl_consumer_presence.py","sha256":"001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778"},"verify_image_component_presence.py":{"bytes":11840,"path":"scripts/verify_image_component_presence.py","sha256":"36e92fdfd3105a9db6bc20fd75aade1238a5e0b053a581bfb24c7b5e1bf6a6d3"}},"image_source_commit":"665220a2b5086597daa4565eef6450a37aaf47f4","security_accepted":false}},"run_id":40196208692,"sha256":"5cd1ab49b517bcd04c7adc1e883ecf351020f96b16ccfec70278037c5c013e99"},"engine_sha256":"1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e","existing_tar_identity":[2049,414928,33152,0,0,1,452719104,1792933419468181620,1792933419468181620],"export":{"activation_performed":false,"archive":{"bytes":183695801,"format":"docker-archive+gzip","sha256":"8fa3f5654d9afb492f5207e6f6cd73b3f9dbd371631ed880e8e5b766875b39ec","uncompressed_bytes":452719104,"uncompressed_sha256":"e10090370762baf289dd55457f648b5c78b05e8e398c724397336b1d89006c67"},"full_config_compared_in_ram":true,"image_config_id":"sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1","image_id":"sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1","input_signature":"a63abda5a1b5fd889575be09d0cdff0412ad0c0cd062f85336fedf0237cf87a2","inputs":{"image-build.json":{"bytes":3028,"sha256":"771afaa1b71e62b585226e51a63bdd1292693c4abfe506fc3b93ed15e546c9d3"},"input-manifest.json":{"bytes":263263,"sha256":"8508eab54e6ca8be95c5ce8568ebb6a9554990b2c1e16604763b13549affb7fe"},"source-identity.json":{"bytes":458507,"sha256":"56d977cc6b7b6244981916a1f3584b455f3ee7ebc216ee6fbad584ef0b067a63"}},"native_abi_checks":{"checks":["trust","psycopg2","sqlite_libmagic","pip_removed"],"completed_with_build":true,"source":"ordinary_Dockerfile_RUN"},"physical_archive_admission_complete":false,"platform":"linux/amd64","raw_counts":{"CRITICAL":0,"HIGH":44,"LOW":82,"MEDIUM":68,"UNKNOWN":1},"reports":{"sbom.cdx.json":{"bytes":295660,"sha256":"d1e7bd9a3f5f57e381295e40ed4b9a439197a3d87899cb558f8b53915cf3c34d"},"vulnerabilities.json":{"bytes":1004829,"sha256":"7eb85d9468148e16d9b228dbbd760841025d3b75357a2c10aa63a839887df27f"}},"rootfs":{"Layers":["sha256:a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7","sha256:535a5d38abecfac4fa49da33fa942b53c53c463bd0d39a860967e46eb03faafe","sha256:6972161a9a77e7c1afb997daac15abc54f5b70c7b40b814601f53e038d8c398c","sha256:d86e9bd004831a6069b9a02e5caac80b73526315f52719079b4017b099956ed2","sha256:3e204cd21b2edfd2cf9d77a1d2032725307c8f01043e2eca16f35e801742f66b","sha256:8de59326698e4655f63fd4e5532c474f3abafda7e32edded25050d5f9eea5820","sha256:a842894c2b3256a955801a4f3d634ade21c3a93f5f9b5aaf58193576d16655e0","sha256:c9da23d02bc39c438741e196f1950f1a30107f71bb7192c6af91450ea696f764","sha256:d31cd1c8922519c5e60c0396430645de0b71973b82136600b382783d11c0b0c8","sha256:457975dd4444c78af6aac81f1d4b44585052c94aa52a5d942acbdb8561154142","sha256:8183b6021d6775b488b267abaa469f2dc86e7b73b0a5dd5d3daef9172d0e8074","sha256:ab4640e9ff51275ad55673a6ba12dd1c7f61512e5d3a5f0c524c7ecbf8130467","sha256:f66767c7dd83565dcccbbaa6d524faa79a0aa4cee3de2978e2c77e9ac0799b26","sha256:9539b4fb7d1f332da6d1d041718d0ea6c0312f272fe2b37b0121274363ea35d9","sha256:d365532c6abd0b186bf0b5b510bb0dffe2ca8c912e6ef7f15b19dcb5d39bad20","sha256:aba3d55fbc0cd22b6762952e92c7a46892ca2d889c07665719c7fe04225479cf","sha256:ab7840770e7d2a526cb62900bc9e5dcdfa2454dbf6c6531c13da84e70544bf4d","sha256:093ccb908184c22066df90e52fc388e3f26f6cd8ec435b9a7b7be2f0cb9ec0d1","sha256:29b52a15e9e6677ffad8799b8eec656f250035ea1a7403c336cd652aad6cdc8b","sha256:b06c2c3dd56d631de5deda8744e78b95360c6f31edabfef65643d78f709a0ffc","sha256:9f19d62d62ec6c2b527d73594454648dbd772bc40114fdcf019ed9605b5085bb","sha256:a4cd6d8ce7054acdf3fea36cbc0ee6243337b50b5ef7502727cba8c4bd9dc329","sha256:d29d88236a8b8f0f46a61014304fdeb828ab7a9802c6105048a32f2b1760cede","sha256:db5de29cb9291c166a771b93fd64dc577f1e5de6e935e7178bf645becd7ae175","sha256:00aff1708af51ca851723d52ee94b49c13bf2596c6948ceb344ba51d1fe89f8c","sha256:2014b2e5edc6c1820bc23801c6f6020fdb0b53abe1170d7edbe3ac24d5393b3c","sha256:b8edb16e8cc1f34718d93054e59e40fafcf727807affaecffc9d10309d2d7108","sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef","sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef","sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef","sha256:d11e8eb630dda8fb072612381737f8e056c58bd8a412d3274c7694d55239e7a5","sha256:4c632541a034234eb926259de0f177216f0bbc41516e730a9e7b7d38e0106200","sha256:b05a2827ff6e9540948e7eda99ae0410a3364373a4cfb5285ac9422c4f0d8180"],"Type":"layers"},"run_attempt":"1","run_id":"40191605131","schema":"canonical_app_image_export/1","security_accepted":false,"signatures_verified":false,"source_commit":"665220a2b5086597daa4565eef6450a37aaf47f4","source_tree":"f522d42f754599bb9ebd0ea0e284a338ec1dcfbf","status":"exported_not_accepted"},"helpers":{"deploy/maintenance/datastore_candidate_contract.py":{"bytes":38382,"sha256":"d210020e24fedd4986803a3fe2618a5016ecd99f9e3ca9588af02d77a967e17d"},"deploy/maintenance/runtime_image_archive_transport.py":{"bytes":35148,"sha256":"20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2"},"scripts/admit_security_component_image_reuse.py":{"bytes":27518,"sha256":"9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025"},"scripts/compose_market_security_os_candidate.py":{"bytes":52231,"sha256":"c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9"},"scripts/run_canonical_app_ci_smoke.py":{"bytes":35767,"sha256":"265d272878112c69fde7fb3e6bd0ae82b41fee2a0ea454c24e56f43a2ed7ad09"}},"operation":"security-canonical-app-candidate-images-20261025-01","profiles":[{"archive":{"format":"docker-archive+gzip","sha256":"8fa3f5654d9afb492f5207e6f6cd73b3f9dbd371631ed880e8e5b766875b39ec","size_bytes":183695801,"uncompressed_bytes":452719104},"candidate_config_id":"sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1","candidate_rootfs":["sha256:a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7","sha256:535a5d38abecfac4fa49da33fa942b53c53c463bd0d39a860967e46eb03faafe","sha256:6972161a9a77e7c1afb997daac15abc54f5b70c7b40b814601f53e038d8c398c","sha256:d86e9bd004831a6069b9a02e5caac80b73526315f52719079b4017b099956ed2","sha256:3e204cd21b2edfd2cf9d77a1d2032725307c8f01043e2eca16f35e801742f66b","sha256:8de59326698e4655f63fd4e5532c474f3abafda7e32edded25050d5f9eea5820","sha256:a842894c2b3256a955801a4f3d634ade21c3a93f5f9b5aaf58193576d16655e0","sha256:c9da23d02bc39c438741e196f1950f1a30107f71bb7192c6af91450ea696f764","sha256:d31cd1c8922519c5e60c0396430645de0b71973b82136600b382783d11c0b0c8","sha256:457975dd4444c78af6aac81f1d4b44585052c94aa52a5d942acbdb8561154142","sha256:8183b6021d6775b488b267abaa469f2dc86e7b73b0a5dd5d3daef9172d0e8074","sha256:ab4640e9ff51275ad55673a6ba12dd1c7f61512e5d3a5f0c524c7ecbf8130467","sha256:f66767c7dd83565dcccbbaa6d524faa79a0aa4cee3de2978e2c77e9ac0799b26","sha256:9539b4fb7d1f332da6d1d041718d0ea6c0312f272fe2b37b0121274363ea35d9","sha256:d365532c6abd0b186bf0b5b510bb0dffe2ca8c912e6ef7f15b19dcb5d39bad20","sha256:aba3d55fbc0cd22b6762952e92c7a46892ca2d889c07665719c7fe04225479cf","sha256:ab7840770e7d2a526cb62900bc9e5dcdfa2454dbf6c6531c13da84e70544bf4d","sha256:093ccb908184c22066df90e52fc388e3f26f6cd8ec435b9a7b7be2f0cb9ec0d1","sha256:29b52a15e9e6677ffad8799b8eec656f250035ea1a7403c336cd652aad6cdc8b","sha256:b06c2c3dd56d631de5deda8744e78b95360c6f31edabfef65643d78f709a0ffc","sha256:9f19d62d62ec6c2b527d73594454648dbd772bc40114fdcf019ed9605b5085bb","sha256:a4cd6d8ce7054acdf3fea36cbc0ee6243337b50b5ef7502727cba8c4bd9dc329","sha256:d29d88236a8b8f0f46a61014304fdeb828ab7a9802c6105048a32f2b1760cede","sha256:db5de29cb9291c166a771b93fd64dc577f1e5de6e935e7178bf645becd7ae175","sha256:00aff1708af51ca851723d52ee94b49c13bf2596c6948ceb344ba51d1fe89f8c","sha256:2014b2e5edc6c1820bc23801c6f6020fdb0b53abe1170d7edbe3ac24d5393b3c","sha256:b8edb16e8cc1f34718d93054e59e40fafcf727807affaecffc9d10309d2d7108","sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef","sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef","sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef","sha256:d11e8eb630dda8fb072612381737f8e056c58bd8a412d3274c7694d55239e7a5","sha256:4c632541a034234eb926259de0f177216f0bbc41516e730a9e7b7d38e0106200","sha256:b05a2827ff6e9540948e7eda99ae0410a3364373a4cfb5285ac9422c4f0d8180"],"ci_run_id":40191605131,"image_artifact":{"digest":"sha256:46b1e1f0d826493db7d18b7835acccdb75af4282ac9d504dc21d842e8608b8d6","id":12598511333,"size_in_bytes":183170277},"profile":"canonical_app","role":"app","security_accepted":false,"tar_sha256":"e10090370762baf289dd55457f648b5c78b05e8e398c724397336b1d89006c67"}],"public_evidence":{"bytes":336678,"sha256":"eac66d11595ae9bd7a4478f5b126ac77e4d36b81dab25613f0d951c833d7b653"},"schema":"canonical_app_image_store_catalog/1"}\n'

def need(ok,code):
    if not ok:raise RuntimeError(code)

def refusal_binding(args,stack,M):
    """Cold public control/worker binding for REFUSAL only, never authority."""
    if args.site!='web' or args.phase!='stream-web':return None
    M.need(type(args.control_sha256) is str and re.fullmatch('[a-f0-9]{64}',args.control_sha256),'control_scope')
    raw,control_check=M.seal(stack,args.control,args.control_sha256,M.LIMIT);op=M.document(raw)
    M.need(op.get('schema')=='canonical_app_image_store_control/1' and op.get('operation')==OPERATION
        and op.get('phase')=='load-native' and op.get('profile')=='canonical_app'
        and type(op.get('nonce')) is str and re.fullmatch('[a-f0-9]{32}',op['nonce'])
        and args.control==ROOTS['web']/'controls'/(op['nonce']+'.json')
        and type(op.get('worker_sha256')) is str and re.fullmatch('[a-f0-9]{64}',op['worker_sha256']),'control_scope')
    source=Path(__file__).resolve();M.need(source==ROOTS['web']/WORKER,'source_path')
    _,worker_check=M.seal(stack,source,op['worker_sha256'],131072)
    return {'operation':OPERATION,'profile':'canonical_app','nonce':op['nonce'],'control_sha256':args.control_sha256,
        'image_config_id':catalog_contract()['export']['image_config_id']},(control_check,worker_check)

def refusal_record(M,binding,error):
    base,checks=binding
    for check in checks:check()
    code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in M.PUBLIC_CODES else 'refused_or_unknown'
    stage=M.SUBPHASE if M.SUBPHASE in M.PEER_SUBPHASES else 'control_admission'
    return {**base,'schema':'canonical_app_preheader_refusal/1','phase':'stream-web','subphase':stage,'code':code,
        'retry_allowed':False,'security_accepted':False,**M.exception_metadata(error)}

def admit_refusal(ctx,value):
    M=ctx.engine;fields={'schema','operation','profile','nonce','control_sha256','image_config_id','phase','subphase','code',
        'retry_allowed','security_accepted','exception_class','os_errno'}
    M.need(type(value) is dict and set(value)==fields and value['schema']=='canonical_app_preheader_refusal/1'
        and value['operation']==OPERATION and value['profile']==ctx.spec['profile']=='canonical_app'
        and value['nonce']==ctx.op['nonce'] and value['control_sha256']==ctx.pin
        and value['image_config_id']==ctx.spec['candidate_config_id'] and value['phase']=='stream-web'
        and type(value['subphase']) is str and value['subphase'] in M.PEER_SUBPHASES
        and type(value['code']) is str and value['code'] in M.PUBLIC_CODES|{'refused_or_unknown'}
        and type(value['exception_class']) is str and value['exception_class'] in M.PEER_CLASSES
        and (value['os_errno'] is None or type(value['os_errno']) is int and 0<value['os_errno']<=4095)
        and value['retry_allowed'] is False and value['security_accepted'] is False,'stream_binding')
    return dict(value)

def emit_refusal(M,value,fd=1):
    """Single <=4KiB nonblocking frame, <=1s; no capacity/command recursion."""
    if M.app_stream_frame_attempted:return False
    target=os.fstat(fd);need(stat.S_ISFIFO(target.st_mode),'app_evidence')
    raw=M.encode(value);need(0<len(raw)<=4092,'app_evidence');raw=len(raw).to_bytes(4,'big')+raw
    M.app_stream_frame_attempted=True;end=time.monotonic()+1
    out=os.open('/proc/self/fd/'+str(fd),os.O_WRONLY|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        s=os.fstat(out);need(stat.S_ISFIFO(s.st_mode) and (s.st_dev,s.st_ino)==(target.st_dev,target.st_ino)
            and len(raw)<=os.fpathconf(out,'PC_PIPE_BUF'),'app_evidence')
        while time.monotonic()<end:
            _,ready,_=select.select([],[out],[],min(.05,max(0,end-time.monotonic())))
            if ready:
                try:return os.write(out,raw)==len(raw)
                except BlockingIOError:pass
        return False
    finally:os.close(out)

@contextmanager
def engine_source(path):
    """Bootstrap only the fixed public engine; retain its FD and parent proof."""
    ancestor=[]
    for p in path.parents:
        s=p.lstat();need(p.resolve(strict=True)==p and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0
            and (not s.st_mode&0o022 or p==Path('/tmp') and stat.S_IMODE(s.st_mode)==0o1777),'protected_parent')
        ancestor.append((p,(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)))
    need(path.resolve(strict=True)==path,'source_path');fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    try:
        before=identity(os.fstat(fd));need(before[2:6]==(0o100600,0,0,1) and 0<before[6]<=131072,'source_metadata')
        raw=os.pread(fd,before[6]+1,0);need(len(raw)==before[6] and sha256(raw).hexdigest()==ENGINE_SHA,'source_sha256')
        def check():
            need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in ancestor),'protected_parent_changed')
            need(before==identity(os.fstat(fd))==identity(path.lstat()) and sha256(os.pread(fd,before[6]+1,0)).hexdigest()==ENGINE_SHA,'source_changed')
        check();primary=False
        try:yield raw,check
        except BaseException:primary=True;raise
        finally:
            try:check()
            except Exception:
                if not primary:raise
    finally:os.close(fd)

def inventory(M,site,rows):
    count,running=(15,15) if site=='bot' else (27,16)
    M.need(type(rows) is list and len(rows)==count and len({r['id'] for r in rows})==count
        and all(type(r['running']) is bool for r in rows) and sum(r['running'] for r in rows)==running,'container_inventory')

def catalog_contract():
    need(sha256(CATALOG_SOURCE).hexdigest()==CATALOG_SHA,'app_catalog')
    return json.loads(CATALOG_SOURCE)

def zip_selected(raw,names,selected,maximum=8*1024**2):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows=archive.infolist();need(len(rows)==len(names) and {r.filename for r in rows}==set(names)
            and all(not r.is_dir() and not r.flag_bits&1 and stat.S_IFMT(r.external_attr>>16) in (0,stat.S_IFREG)
                and 0<r.file_size<=maximum for r in rows),'app_evidence')
        values={}
        for name in selected:
            item=archive.getinfo(name)
            with archive.open(item) as source:body=source.read(maximum+1)
            need(len(body)==item.file_size<=maximum,'app_evidence');values[name]=body
        return values

def selected_evidence(ctx):
    """Authenticated public image Config stays private to this operation's RAM."""
    if hasattr(ctx,'app_evidence'):return ctx.app_evidence
    M=ctx.engine;catalog=ctx.catalog;actual=catalog['actual_ci'];known=catalog['export']
    selected=set(actual['records'])|{'evidence/public-evidence.zip'}
    raw,check=M.seal(ctx.stack,ctx.root/'evidence/canonical-smoke.zip',actual['sha256'],1048576);ctx.checks.append(check)
    M.need(len(raw)==actual['bytes'],'app_ci_gate');files=zip_selected(raw,actual['members'],selected)
    for name,expected in actual['records'].items():
        M.need(sha256(files[name]).hexdigest()==actual['record_sha256'][name]
            and ctx.D.same(ctx.H.document(files[name]),expected),'app_ci_gate')
    evidence=catalog['public_evidence'];raw,check=M.seal(ctx.stack,ctx.root/'evidence/canonical-build.zip',evidence['sha256'],1048576)
    ctx.checks.append(check);M.need(len(raw)==evidence['bytes'] and files['evidence/public-evidence.zip']==raw,'app_evidence')
    names=ctx.H.EVIDENCE_NAMES
    small=zip_selected(raw,names,{'reports/vulnerabilities.json','reports/sbom.cdx.json'})
    report=ctx.H.document(small['reports/vulnerabilities.json']);metadata=report['Metadata'];image=metadata['ImageConfig']
    M.need(type(image) is dict and set(image)=={'architecture','config','created','history','os','rootfs'}
        and image['architecture']=='amd64' and image['os']=='linux' and metadata['ImageID']==known['image_config_id']
        and type(image['config']) is dict and set(image['config'])=={'Cmd','Env','Labels','WorkingDir'}
        and ctx.D.same(image['rootfs'],{'type':'layers','diff_ids':known['rootfs']['Layers']}),'app_evidence')
    ctx.D.container_config(image['config'])
    # The fixed whole ZIP and exact CI records authenticate all source/input,
    # report, 32-case, migration and cleanup facts; never substitute future CI.
    ctx.app_evidence={'spec':{'profile':'canonical_app','config_id':known['image_config_id'],
        'layers':known['rootfs']['Layers'],'archive':{'uncompressed_bytes':ctx.spec['archive']['uncompressed_bytes']}},
        'image':{'Config':image['config'],'Comment':ctx.D.expected_inspect_comment(image),
            'Author':image.get('author',''),'DockerVersion':image.get('docker_version','')},
        'ci_proof':actual['records']['reports/canonical-archive-admission.json'],
        'ci_archive_proof_sha256':actual['record_sha256']['reports/canonical-archive-admission.json']}
    return ctx.app_evidence

def source_archive(ctx):
    M=ctx.engine;M.need(ctx.op['admission']=='canonical_app-admission.json','app_admission')
    raw,check=M.seal(ctx.stack,ctx.root/'controls'/ctx.op['admission'],ctx.op['admission_sha256'],M.LIMIT);ctx.checks.append(check)
    value=M.document(raw);base={'schema','operation','profile','candidate_config_id','archive_bytes','archive_sha256',
        'tar_identity','rootfs','source_identities','expanded_layer_bytes','prospective_import_bytes','operation_sha256',
        'physical_graph_closed','whole_typed_config_compared_in_ram','ci_archive_proof_sha256','actual_ci_artifact_sha256',
        'configuration_values_disclosed','security_accepted'}
    optional={'f79_restart_observation','processor_health_observation'}
    M.need(type(value) is dict and base<=set(value)<=base|optional and value['schema']=='canonical_app_archive_admission/1'
        and value['operation']==OPERATION and value['profile']=='canonical_app' and value['candidate_config_id']==ctx.spec['candidate_config_id']
        and value['physical_graph_closed'] is True and value['whole_typed_config_compared_in_ram'] is True
        and value['configuration_values_disclosed'] is False and value['security_accepted'] is False,'app_admission_shape')
    data=selected_evidence(ctx);proof=data['ci_proof']
    M.need(type(value['operation_sha256']) is str and re.fullmatch('[a-f0-9]{64}',value['operation_sha256'])
        and type(value['archive_bytes']) is int and value['archive_bytes']==proof['archive_bytes']==ctx.spec['archive']['uncompressed_bytes']
        and value['archive_sha256']==proof['archive_sha256']==ctx.spec['tar_sha256']
        and ctx.D.same(value['tar_identity'],ctx.catalog['existing_tar_identity'])
        and ctx.D.same(value['rootfs'],ctx.spec['candidate_rootfs']) and ctx.D.same(value['source_identities'],proof['source_identities'])
        and type(value['expanded_layer_bytes']) is int and value['expanded_layer_bytes']==proof['expanded_layer_bytes']
        and type(value['prospective_import_bytes']) is int and value['prospective_import_bytes']==2*proof['archive_bytes']+2*proof['expanded_layer_bytes']
        and value['ci_archive_proof_sha256']==data['ci_archive_proof_sha256']
        and value['actual_ci_artifact_sha256']==ctx.catalog['actual_ci']['sha256'],'app_admission')
    return value

def retained_tar(ctx):
    M=ctx.engine;M.need(ctx.site=='web','site_scope');ancestor=M.parents(EXISTING_TAR)
    fd,size,check=ctx.stack.enter_context(ctx.D.held(EXISTING_TAR,ctx.spec['archive']['uncompressed_bytes']))
    # Run the saved parent proof while the original archive FD is still held.
    ctx.stack.callback(M.check_parents,ancestor);ctx.checks.extend((check,lambda:M.check_parents(ancestor)))
    M.need(ctx.D.same(M.identity(os.fstat(fd)),ctx.catalog['existing_tar_identity'])
        and size==ctx.spec['archive']['uncompressed_bytes'] and ctx.D.digest_fd(fd,size,ctx.end)==ctx.spec['tar_sha256'],'app_archive')
    return fd,size

def admitted_tar(ctx):
    admitted=source_archive(ctx);fd,size=retained_tar(ctx);ctx.budget=admitted['prospective_import_bytes'];ctx.capacity()
    return fd,admitted

def admit_existing_web(ctx):
    M=ctx.engine;M.SUBPHASE='archive_graph_validation';data=selected_evidence(ctx);fd,size=retained_tar(ctx)
    stream=os.fdopen(os.dup(fd),'rb');ctx.stack.callback(stream.close)
    ctx.H.archive_config(stream,size,ctx,ctx.catalog['export'])
    stream.seek(0);proof=ctx.D.validate_archive(stream,size,ctx.end,data,ctx.T,ctx.L)
    M.need(ctx.D.same(proof,data['ci_proof']),'app_archive')
    ctx.budget=2*proof['archive_bytes']+2*proof['expanded_layer_bytes'];ctx.capacity()
    M.need(M.stable(ctx)['image_map']==ctx.baseline['image_map'],'image_map_changed')
    receipt={'schema':'canonical_app_archive_admission/1','operation':OPERATION,'profile':'canonical_app',
        'candidate_config_id':ctx.spec['candidate_config_id'],'archive_bytes':size,'archive_sha256':proof['archive_sha256'],
        'tar_identity':list(ctx.catalog['existing_tar_identity']),'rootfs':proof['rootfs_layers'],'source_identities':proof['source_identities'],
        'expanded_layer_bytes':proof['expanded_layer_bytes'],'prospective_import_bytes':ctx.budget,'operation_sha256':ctx.pin,
        'physical_graph_closed':True,'whole_typed_config_compared_in_ram':True,
        'ci_archive_proof_sha256':data['ci_archive_proof_sha256'],'actual_ci_artifact_sha256':ctx.catalog['actual_ci']['sha256'],
        'configuration_values_disclosed':False,'security_accepted':False,**M.observation_details(ctx)}
    ctx.check();return {'status':'archive_admitted_not_loaded','receipt_sha256':ctx.publish('admission.json',receipt),
        'image_load_requested':False,'security_accepted':False}

def run(ctx,apply=False):
    M=ctx.engine
    if ctx.op['phase']!='admit-existing-web':return M.run(ctx,apply)
    M.SUBPHASE='public_preflight';M.need(M.daemon_policy(ctx)==ctx.baseline['daemon'],'daemon_public_policy_changed')
    with Path('/etc/machine-id').open('rb') as source:host=source.read(65).strip().decode()
    M.need(host==ctx.baseline['machine_id'],'host_identity_changed');M.need(M.store_view(ctx)==ctx.baseline['store'],'docker_store_changed')
    M.need(M.stable(ctx)['image_map']==ctx.baseline['image_map'],'image_map_changed');ctx.capacity()
    if not apply:return {'status':'public_preflight_passed_not_requested','image_load_requested':False,'security_accepted':False}
    M.SUBPHASE='owned_fences';guard=M.Guard(ctx);ctx.begin()
    if ctx.site=='web':result=admit_existing_web(ctx)
    else:
        M.SUBPHASE='web_phase_request';ctx.publish('web-request.json',{'outcome':'unknown_until_measured','retry_allowed':False,'image_load_requested':False,'security_accepted':False})
        ctx.requested=True;rc,raw=M.command_result(ctx,M.remote(ctx,'admit-existing-web'));ctx.peer_returncode=rc
        if rc:
            try:ctx.peer_failure=M.peer_failure(ctx,M.document(raw),rc)
            except Exception:pass
            M.need(False,'command_outcome_unknown')
        result=M.document(raw)
        M.need(type(result) is dict and result['status']=='archive_admitted_not_loaded' and result['image_load_requested'] is False
            and result['security_accepted'] is False and re.fullmatch('[a-f0-9]{64}',result['receipt_sha256']),'remote_result')
        for key in ('f79_restart_observation','processor_health_observation'):
            if key in result:
                validate=M.restart_observation if key=='f79_restart_observation' else M.processor_health_observation
                M.need(validate(ctx,result[key]) is not None,'remote_result');setattr(ctx,'peer_'+key,result[key])
        M.need(M.stable(ctx)['image_map']==ctx.baseline['image_map'],'native_runtime_changed')
    ctx.check();M.need(M.daemon_policy(ctx)==ctx.baseline['daemon'],'daemon_public_policy_changed')
    M.need(M.store_view(ctx)==ctx.baseline['store'],'docker_store_changed');guard.finish();return result

def configure(M,contract,checks):
    """Private ModuleType only; common binary transport and load remain intact."""
    M.OPERATION=OPERATION;M.CONFIRM=CONFIRM;M.TOOLS=dict(ROOTS);M.CATALOG_SHA=CATALOG_SHA;M.PHASES=PHASES
    M.runtime_contract=contract;M.bootstrap_checks=list(checks);M.PUBLIC_CODES=M.PUBLIC_CODES|PUBLIC_CODES
    M.app_stream_frame_attempted=False;original_frame,original_header,original_failure=M.frame,M.header,M.failure_details
    def frame(ctx,fd,value):
        if fd==1:M.app_stream_frame_attempted=True
        return original_frame(ctx,fd,value)
    def header(ctx,value,admitted,*,complete=False):
        first=not complete and not getattr(ctx,'app_header_attempted',False);ctx.app_header_attempted=True
        if first and type(value) is dict and value.get('schema')=='canonical_app_preheader_refusal/1':
            ctx.app_peer_preheader_refusal=admit_refusal(ctx,value);M.need(False,'app_peer_preheader_refused')
        return original_header(ctx,value,admitted,complete=complete)
    def failure(ctx):
        out=original_failure(ctx)
        if ctx is not None and hasattr(ctx,'app_peer_preheader_refusal'):out['app_peer_preheader_refusal']=admit_refusal(ctx,ctx.app_peer_preheader_refusal)
        return out
    M.frame=frame;M.header=header;M.failure_details=failure
    snapshot,remote,image=M.snapshot,M.remote,M.image
    def observe(ctx,admission=None):
        value=snapshot(ctx,admission);inventory(M,ctx.site,value['containers']);return value
    def peer(ctx,phase):
        argv=remote(ctx,phase);old=str(ROOTS['web']/'market_candidate_images.py');M.need(argv.count(old)==1,'source_path')
        return [str(ROOTS['web']/WORKER) if x==old else x for x in argv]
    def measured(ctx,ident,admission):
        return {**image(ctx,ident,admission),'schema':'canonical_app_preloaded_image/1','operation':OPERATION,
            'nonce':ctx.op['nonce'],'controller_sha256':ctx.op['worker_sha256'],'engine_sha256':ENGINE_SHA,
            'actual_ci_run_id':ctx.catalog['actual_ci']['run_id'],'actual_ci_artifact_sha256':ctx.catalog['actual_ci']['sha256'],
            'source_archive_bytes':admission['archive_bytes'],'source_identities':admission['source_identities'],
            'physical_graph_closed':True,'runtime_policy_preserved':True,'serving_app_roles_unchanged':True,
            'runtime_health_issues_open':True,'capture_issue_open':True,'serving_validation':'NOT_TESTED'}
    M.snapshot=observe;M.remote=peer;M.selected_evidence=selected_evidence;M.source_archive=source_archive
    M.admitted_tar=admitted_tar;M.image=measured;M.legacy_a37=lambda ctx,admitted:False
    return M

def load_engine(stack,site):
    root=ROOTS[site];path=root/'source/market_candidate_images.py'
    raw,check=stack.enter_context(engine_source(path));M=ModuleType('_canonical_app_images_engine01');M.__file__=str(path)
    exec(compile(raw,str(path),'exec'),M.__dict__)
    name='datastore_candidate_contract.py';pin=HELPERS['deploy/maintenance/'+name]['sha256']
    raw,contract_check=M.seal(stack,root/'source'/name,pin);D=ModuleType('_canonical_app_images_contract01');D.__file__=str(root/'source'/name)
    exec(compile(raw,D.__file__,'exec'),D.__dict__)
    return configure(M,D,(check,contract_check))

def context(args,stack,M):
    class Context(M.Context):
        def __init__(self):
            M.need(os.geteuid()==os.getegid()==0,'root_required')
            self.engine=M;self.args,self.stack,self.site=args,stack,args.site
            self.root=ROOTS[self.site];self.checks=list(M.bootstrap_checks);self.budget=0;self.requested=False
            info=self.root.lstat();M.need(self.root.resolve(strict=True)==self.root and stat.S_ISDIR(info.st_mode)
                and info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o700,'tools_directory')
            raw,check=M.seal(stack,self.root/'canonical_app_candidate_images.json',CATALOG_SHA);self.checks.append(check)
            self.catalog=M.document(raw);M.need(M.runtime_contract.same(self.catalog,catalog_contract()),'catalog_binding')
            self.modules={}
            for path,pin in self.catalog['helpers'].items():
                name=Path(path).name;raw,check=M.seal(stack,self.root/'source'/name,pin['sha256']);self.checks.append(check)
                M.need(len(raw)==pin['bytes'],'source_metadata');module=ModuleType('_datastore_images_'+Path(name).stem)
                module.__file__=str(self.root/'source'/name);exec(compile(raw,module.__file__,'exec'),module.__dict__);self.modules[name]=module
            self.D=self.modules['datastore_candidate_contract.py'];self.T=self.modules['runtime_image_archive_transport.py']
            self.L=self.modules['admit_security_component_image_reuse.py'];self.M=self.modules['compose_market_security_os_candidate.py']
            self.H=self.modules['run_canonical_app_ci_smoke.py']
            self.D.legacy_configs=lambda config,moby:self.H.canonical_legacy_configs(config,moby,self.D)
            raw,check=M.seal(stack,args.control,args.control_sha256,M.LIMIT);self.checks.append(check);self.op=M.document(raw);self.pin=args.control_sha256
            op=self.op;M.need(set(op)=={'schema','operation','phase','profile','nonce','not_before','deadline','worker_sha256',
                'baselines','fences','admission','admission_sha256'},'control_fields')
            M.need(op['schema']=='canonical_app_image_store_control/1' and op['operation']==OPERATION and op['phase'] in M.PHASES
                and type(op['nonce']) is str and re.fullmatch('[a-f0-9]{32}',op['nonce'])
                and args.control==self.root/'controls'/(op['nonce']+'.json'),'control_scope')
            M.need(set(op['baselines'])==set(op['fences'])=={'bot','web'},'baseline_sites')
            M.need(self.D.same(op['fences']['bot'].get('source'),SOURCE_ID),'permanent_fence_identity')
            M.need(type(op['not_before']) is int and type(op['deadline']) is int
                and op['not_before']<=time.time()<op['deadline']<=op['not_before']+900,'deadline')
            self.end=time.monotonic()+op['deadline']-time.time()
            raw,check=M.seal(stack,self.root/WORKER,op['worker_sha256']);self.checks.append(check)
            chosen=[s for s in self.catalog['profiles'] if s['profile']==op['profile']];M.need(len(chosen)==1,'profile_scope')
            self.spec={**chosen[0],'site':'native' if op['phase']=='load-native' else 'web'}
            M.need(args.phase==op['phase'] or self.site=='web' and args.phase=='stream-web' and op['phase']=='load-native','phase_scope')
            self.work=self.root/'attempts'/(op['profile']+'-'+op['phase']);self.baseline=op['baselines'][self.site]
            for site,b in op['baselines'].items():
                M.need(b['schema']=='canonical_app_image_store_baseline/1' and b['operation']==OPERATION and b['site']==site
                    and b['controller_sha256']==op['worker_sha256'] and b['runtime_health_issues_open'] is True
                    and b['security_accepted'] is False,'baseline_scope');inventory(M,site,b['containers'])
                M.need(type(b['observed_at']) is int and 0<=time.time()-b['observed_at']<=300,'baseline_freshness')
            M.scope(self.site,args.phase,op['nonce']);self.check()
            unit='market-images-'+self.site+'-'+args.phase+'-'+op['nonce']+'.scope'
            values=M.command(self,['/usr/bin/systemctl','show',unit,'--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL'],maximum=4096).decode()
            values=dict(line.split('=',1) for line in values.splitlines())
            M.need(set(values)=={'RuntimeMaxUSec','TimeoutStopUSec','KillMode','SendSIGKILL'}
                and values['RuntimeMaxUSec'] in {'15min','15min 0s','900s'} and values['TimeoutStopUSec']=='5s'
                and values['KillMode']=='control-group' and values['SendSIGKILL']=='yes','scope_lifetime')
    return Context()

def collect(M,site,source_pin):
    M.SUBPHASE='snapshot_source_admission';M.need(os.geteuid()==os.getegid()==0,'root_required')
    with ExitStack() as stack:
        _,check=M.seal(stack,ROOTS[site]/WORKER,source_pin)
        class Observer:
            def check(self):
                M.need(time.monotonic()<self.end,'deadline');check()
                for held in M.bootstrap_checks:held()
            capacity=check
        ctx=Observer();ctx.site=site;ctx.end=time.monotonic()+60
        store=M.store_view(ctx)
        with Path('/etc/machine-id').open('rb') as f:host=f.read(65).strip().decode()
        M.need(re.fullmatch('[a-f0-9]{32}',host),'host_identity')
        before=M.snapshot(ctx);daemon=M.daemon_policy(ctx);temporary=M.temp_metadata();after=M.snapshot(ctx)
        M.need(M.runtime_contract.same(before,after),'snapshot_runtime_unstable');ctx.check()
        return {**after,'schema':'canonical_app_image_store_baseline/1','operation':OPERATION,'observed_at':int(time.time()),
            'site':site,'machine_id':host,'store':store,'daemon':daemon,'temporary_storage':temporary,
            'controller_sha256':source_pin,'runtime_health_issues_open':True,'security_accepted':False}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site',choices=('bot','web'),required=True)
    parser.add_argument('--phase',choices=(*PHASES,'stream-web','snapshot'),required=True)
    parser.add_argument('--control',type=Path);parser.add_argument('--control-sha256');parser.add_argument('--source-sha256');parser.add_argument('--apply')
    args=parser.parse_args(argv);M=None;ctx=None;binding=None;stack=ExitStack()
    try:
        M=load_engine(stack,args.site);M.need(args.apply in (None,CONFIRM),'confirmation')
        binding=refusal_binding(args,stack,M)
        if args.phase=='snapshot':
            M.need(args.apply is None,'snapshot_readonly');result=collect(M,args.site,args.source_sha256)
        else:
            ctx=context(args,stack,M);M.signal.alarm(max(1,int(ctx.end-time.monotonic())))
            selected_evidence(ctx);result=run(ctx,args.apply is not None)
            if result is not None:result={**result,**M.observation_details(ctx)}
        if result is not None:M.public(result)
        stack.close()
        if result is not None:print(json.dumps(result,sort_keys=True))
        return 0
    except Exception as error:
        code=error.args[0] if M is not None and type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in M.PUBLIC_CODES else 'refused_or_unknown'
        if code=='refused_or_unknown' and ctx is not None:code=ctx.D.error_code(error)
        if args.phase=='stream-web':
            if M is not None and binding is not None and not M.app_stream_frame_attempted:
                try:emit_refusal(M,refusal_record(M,binding,error))
                except Exception:pass  # A refusal diagnostic never replaces the primary failure.
        else:
            details=M.exception_metadata(error) if M is not None else {'exception_class':'other','os_errno':None}
            print(json.dumps({'status':'refused_or_unknown','phase':args.phase,'subphase':getattr(M,'SUBPHASE','control_admission'),
                'code':code,'retry_allowed':False,'security_accepted':False,**details,**(M.failure_details(ctx) if M is not None else {})},sort_keys=True))
        return 1
    finally:
        if M is not None:M.signal.alarm(0)
        try:stack.close()
        except Exception:pass  # Success already completed closure inside the try; retain a primary failure.

if __name__=='__main__':raise SystemExit(main())
