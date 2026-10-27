"""Read only the admitted public Market source; no project import or data API."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

MANIFEST_SHA = 'fef6305b1d7e6db851d1d0b77f3aa9910aad535e83f64f012790165a3d62c81b'
ROWS_SHA = '75d7d2cfb2c20fd036501e7aac198cb5567fb82c53301fb3972f8c81b935e57f'
ROWS = [{"bytes":6062,"git_blob":"d8de5f5fb172d4704546b07a0016054af505ce1c","mode":"100644","path":"config/market_data_sources.v1.json","sha256":"847c2218f884e9d2b91d2f5f2efddb048368e926baebac54859ec4eaa61e8015"},{"bytes":9455,"git_blob":"5d92195a0743e1366da9edb60be1ddfe696c25e1","mode":"100644","path":"contracts/market_data/estimator_snapshot-1.0.schema.json","sha256":"2e309252fa2caba7b281d46331bd1cd588f07e1659f59cc9b64bc0af45fb8078"},{"bytes":11571,"git_blob":"c110d26be6dd9c053adbcbf987bc5a2e6af57476","mode":"100644","path":"contracts/market_data/estimator_snapshot-2.0.schema.json","sha256":"10c5a48cd1cc8ad654327cb323f69abdf9e4f913c2348a67b46997f3c750e142"},{"bytes":2660,"git_blob":"dca01d90448d3128051bc4b3d3b2a7af31684fa1","mode":"100644","path":"contracts/market_data/market_capture_record-1.0.schema.json","sha256":"d609d87afa5185cb69006cffbddcdfa9faf9b2df8157f66e134b040055af4b10"},{"bytes":16671,"git_blob":"9dce590e59d5c12fc1f38d6310312ee5fcde0f32","mode":"100644","path":"contracts/market_data/market_fact-1.0.schema.json","sha256":"f46a6d30c8714a43620c91b707432a479cff13b553eea80645f7b5ec0a68f051"},{"bytes":1917,"git_blob":"6d1f61c1757b7d647f8dcbece7a7c0815c6b5a6e","mode":"100644","path":"contracts/market_data/market_fact_ack-1.0.schema.json","sha256":"c941a6e6a865458eed20873a67c6dba3f5a8f42ffb81ed0e76b1dd1af563f8b1"},{"bytes":19840,"git_blob":"420a209649ff1dedcd780b28ab6f066d95112f60","mode":"100644","path":"contracts/market_data/market_fact_batch-1.0.schema.json","sha256":"2acccdf6ffdcabdf275ee6daf4490c0ed7c0e70dfc3096ab37289f3e734e87e5"},{"bytes":3466,"git_blob":"554957d08f7058c843384de524813adebb0b3efe","mode":"100644","path":"contracts/market_data/market_source_registry-1.0.schema.json","sha256":"fe05c88e93a4ca9186f89cc721dfe7ddc25ed1e575627e1ea5585b6e07825097"},{"bytes":0,"git_blob":"e69de29bb2d1d6434b8b29ae775ad8c2e48c5391","mode":"100644","path":"core/__init__.py","sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},{"bytes":631,"git_blob":"d22ea98b276b668ae4aa89270d0420ad290326cb","mode":"100644","path":"core/market_intelligence/__init__.py","sha256":"5d612fcb9e2906d2ea66256e284570f97b9f0d762e2658bac08f3586ade46132"},{"bytes":97196,"git_blob":"78e0f97ee9bba1aec71678c4aee007c1d968a838","mode":"100644","path":"core/market_intelligence/capture_event_adapter.py","sha256":"f8e0a43c29c5c8695cf17146e584d5c9761f32b829f6a65246235498fb9c1b8a"},{"bytes":8814,"git_blob":"8fb2f3f9ff8ee7a1435a1cff7fc9e6e5e0bf25b5","mode":"100644","path":"core/market_intelligence/coin_catalog.py","sha256":"6abd78997a95e68041fb2f5ec82f6a62cb02690634a75776de375cbe79de3318"},{"bytes":46960,"git_blob":"c511dd4d712ac073e5d820c7785d8a2996a469a2","mode":"100644","path":"core/market_intelligence/coin_condition_review.py","sha256":"54c1242d8cc0f30663d9c72296a71c185b88e2224b52601b0988ee29b4a221ba"},{"bytes":8757,"git_blob":"fb6fda22448a7a1f56454c2a654c1c80f1f90d92","mode":"100644","path":"core/market_intelligence/coin_group_calibration_corpus.py","sha256":"fd5c09c2932c403e248bb4c6f4eb6080b818eafc0e039b60261b165ad54e86d4"},{"bytes":24489,"git_blob":"8089d0645eb4f65e6ead5f23c92ed719c26ab18b","mode":"100644","path":"core/market_intelligence/coin_group_feedback.py","sha256":"1729e55ef5bea97c077971a300afb6db8fcbbc9e7014784b81487cd1dd6a3bf7"},{"bytes":9378,"git_blob":"fa7b70b1ab46ad19c3f8a3e0a6903c7dd8b1c219","mode":"100644","path":"core/market_intelligence/coin_group_payloads.py","sha256":"23c79d23919f6e138ea442e00e6c2b4a24caefe739859d7295739304d0ed95ca"},{"bytes":45759,"git_blob":"469a8af1f0fbbd5c480047aa0c8ea2189969f20b","mode":"100644","path":"core/market_intelligence/coin_group_pipeline.py","sha256":"564bd199df9fe1269b26347a8fddf91c00cbe5f6512b136e8759929a8c8cf780"},{"bytes":24946,"git_blob":"169cf2cd4bb1cacf15c5cae5a259d995027147a1","mode":"100644","path":"core/market_intelligence/coin_group_resolution.py","sha256":"e9a98a3330ade857071e1549e2d1e73e7ccf7c838e9fbe5affdcad1e448d991e"},{"bytes":14307,"git_blob":"d523a7190b449536ca6e2c9c780575c7a0c1c547","mode":"100644","path":"core/market_intelligence/coin_group_review_projection.py","sha256":"72f5bb118cbcfcb2dca054f7cec47756067051906b86b200f93f818626832706"},{"bytes":18695,"git_blob":"6aab08b3dd9b391bcfd4a9ab1e436d29ef412751","mode":"100644","path":"core/market_intelligence/coin_group_staging.py","sha256":"6f961c1dada2f449ebc7f4dc872022223635e25886b565991e9fee8eaf463ab6"},{"bytes":32511,"git_blob":"4a2681878a03a92a3251daee2a715fcf52b15d99","mode":"100644","path":"core/market_intelligence/coin_group_trades.py","sha256":"fae24d3d1f8741e0cbd4cfc09bb5ec2433f86718e5e6ffa1fb26c0556659195d"},{"bytes":27372,"git_blob":"d2ff4c218f0052273f083a85344979a51eda84f2","mode":"100644","path":"core/market_intelligence/coin_groups.py","sha256":"b3014dd22779c6e35fb926a232b074d5ea0537108d7f4ba1b58f4b736c49a979"},{"bytes":11728,"git_blob":"c23b2814cde5aa92085883d3f1df40f667010177","mode":"100644","path":"core/market_intelligence/coin_inference.py","sha256":"8005cd02996253234d9bde63e2cb89a182d4a4776b5239c1d4ea66891464a2b5"},{"bytes":8744,"git_blob":"8cee5909d915f2a719adf72dffb76f63b910a52c","mode":"100644","path":"core/market_intelligence/coin_inference_audit.py","sha256":"60e4cf41fda3fbb41c8c5149baeebb405fabc7fac0c44d2dc3dfdcc05775bb21"},{"bytes":5532,"git_blob":"0964b348b095528d0cbf952909180bf05947fc00","mode":"100644","path":"core/market_intelligence/coin_inference_outcome.py","sha256":"31eba55576236cef3b982a6013be9c0591503967c7333297c19670c074a0904d"},{"bytes":10019,"git_blob":"a2ddbc4e6f3e33c0b4a0b04e3146d1a8db4dc3a5","mode":"100644","path":"core/market_intelligence/coin_inference_rollout_metrics.py","sha256":"b44cd20e687e46120c7628377c2a2ec814fda0b3f5c757a017daf30c045ec5f2"},{"bytes":5248,"git_blob":"2f8846522b0ac6f232cc6fd3b145f340021e9f76","mode":"100644","path":"core/market_intelligence/coin_inference_selection.py","sha256":"3c917842ee49a47ddfbf8ad545984908dc131be3703a82fb26fbd4d1a2638544"},{"bytes":8087,"git_blob":"4a7278fa459293658a20872b88ab30fd17f7aae1","mode":"100644","path":"core/market_intelligence/coin_inference_shadow.py","sha256":"c5058a934dba4044ca4b423b6fd130f4f9ef49db3f43a5cc129f8373970ddade"},{"bytes":17246,"git_blob":"9a62e45b86cde77c5aeb0bf02d2c48ee507a6544","mode":"100644","path":"core/market_intelligence/coin_offer_conditions.py","sha256":"a589d5e843740b0f1c0a6e70d7e9b196e589368d8aedb4608abe5a36f9d72af7"},{"bytes":9772,"git_blob":"a1c27684a967e303c91b8d0027d5ec4abfc48c10","mode":"100644","path":"core/market_intelligence/coin_prediction_anchors.py","sha256":"376adce573dd77715809c63add2b5f58e51bfdf79d775d13f3a09c5ad105d4a4"},{"bytes":22361,"git_blob":"2b870a698bdc4f8f879d5c47b102756958f9c31f","mode":"100644","path":"core/market_intelligence/coin_rate_engine.py","sha256":"936f3609ef7047f51d5d6f7e1626aa47dfe46e12034971b8e0c5d30b3b517cb6"},{"bytes":5939,"git_blob":"4be7827d70ea57eef922a299d2d98c253f32fbe0","mode":"100644","path":"core/market_intelligence/coin_relationship_challenger.py","sha256":"7b81f67724984e74cc4888f30cb36e0902ee852bf38b2679fe0cbc22de1aa795"},{"bytes":4850,"git_blob":"3277888d0cbb528fea4d142f05444def947694b0","mode":"100644","path":"core/market_intelligence/coin_relationships.py","sha256":"886ce34b34a45c6482987c7e282566113e2cca31ebfedffb31efd437e864ebaa"},{"bytes":16450,"git_blob":"386dad47e323a79c51b9e3afcb292c1933f46668","mode":"100644","path":"core/market_intelligence/consumed_input_parity.py","sha256":"e96f59a39d2abf4ae2f02ee2a3617302d37df08b35a1bd88e0d6561ced727496"},{"bytes":40346,"git_blob":"c0c22ecf8ec59eeb74687f565cd629265edfad6f","mode":"100644","path":"core/market_intelligence/conversation_quality.py","sha256":"4ed6fb86f157b8f3930aada8681f8bde15e5760b58f9d5d9cd3e163e2bfa0d0e"},{"bytes":80905,"git_blob":"5b7b906877cfa968c5ca28eb09ed26b713a6a850","mode":"100644","path":"core/market_intelligence/estimator_snapshot_receiver.py","sha256":"cc570054d34653de7e04651a7f5d1d48caa1e8618b768b1755c7b7e3abaeef2e"},{"bytes":14346,"git_blob":"d2fbc1f458693076de575b057f5856c785e18cce","mode":"100644","path":"core/market_intelligence/estimator_snapshot_receiver_service.py","sha256":"01c83db29f4e0402760e5037ec9637cd0710ec93ee40c50988175e560b0f97b3"},{"bytes":35287,"git_blob":"39b7bf99ac9f69fde88afe90490b79082e43b2a7","mode":"100644","path":"core/market_intelligence/estimator_snapshot_runtime.py","sha256":"499752c783b5be815cbfe7ec3f209e8b0e9a4189b3a25cd732613e39a3c571e2"},{"bytes":6390,"git_blob":"3ace9be40a0193210370a8d62ab6667dd8093c64","mode":"100644","path":"core/market_intelligence/external_markets.py","sha256":"37a663dee716abd630b1090a31ea86213d9c5d204c6309b4d3c22573d189bab6"},{"bytes":22585,"git_blob":"8581c179eee566fe0b0bad4962a988d1cfd8ab23","mode":"100644","path":"core/market_intelligence/external_quote_capture.py","sha256":"739b0a43246790736eb889b0e63e37d705c4ae1f2ae0a93ceaf2aaa042e5ef59"},{"bytes":4776,"git_blob":"051bdfb9e90c26e703a31641e99a7bb8dcea5fec","mode":"100644","path":"core/market_intelligence/herat_price_normalization.py","sha256":"e1c85c84849c7621c4d28010d0d0511b87cc2cc69742106c018cc2737851bc39"},{"bytes":17884,"git_blob":"60745826878bd5468826533507df6d5ccb272538","mode":"100644","path":"core/market_intelligence/input_health.py","sha256":"d7a5176ed2518c4c8b4c432afbbb2d3bb80d35b16a1eda2223f2f652f9a35e8f"},{"bytes":14226,"git_blob":"0d034e0c160ac4ddac14fdcfed4303bf9d21669b","mode":"100644","path":"core/market_intelligence/market_contracts.py","sha256":"e243361a1039d8f5520b4e1969003f009cb15673d7a9c755fdf09f9c3d5385c5"},{"bytes":59424,"git_blob":"cba507abfc79400f7b628ada74e6eff78d9f7549","mode":"100644","path":"core/market_intelligence/market_fact_adapter.py","sha256":"ded3b627b4b106569a5579b6fccdcaa2ca3ba5b3868325bc4e276417b0d06ebf"},{"bytes":19028,"git_blob":"88e6f75720fc246185a9a4dd165e43bf4676f58c","mode":"100644","path":"core/market_intelligence/market_fact_archive.py","sha256":"789ab01fa9305868d41335a1986d7364eb735b3d0d034ef8667985009d6bd2ba"},{"bytes":32544,"git_blob":"48b5823036a25d8264a743743294c8d83dab1d3f","mode":"100644","path":"core/market_intelligence/market_fact_projection.py","sha256":"c518cb95c57a2ba758cf9474aa6e23476a0dbd2dde53e9fdf229e5ca51e6361a"},{"bytes":24150,"git_blob":"884d6213b1e491c64ccf7f10c8421b3b2eb8ea63","mode":"100644","path":"core/market_intelligence/market_fact_receiver.py","sha256":"a9adf4c5ddba4c6bb82c3ca13e7f88663b9deff9a8d564215ec751244235ee7a"},{"bytes":11752,"git_blob":"e5ed87fd1cdf8a7f58e880ae5edf4fed21f776df","mode":"100644","path":"core/market_intelligence/market_fact_receiver_service.py","sha256":"72114327189b469bbb1cfed608eeb679bf4ac6ea5b94a6312bb75e71199b0676"},{"bytes":24016,"git_blob":"16012047df04b93884267bf267e8f6ed182f22b0","mode":"100644","path":"core/market_intelligence/market_fact_sync.py","sha256":"3e5400c1ea0c8f4caddac0a34443dd0d39a625982e7a1d4bb3c7fd64ebbeb138"},{"bytes":29434,"git_blob":"86cd5c8837b6a1dbb3808bedb8e5fffc26ed15ff","mode":"100644","path":"core/market_intelligence/market_history_backfill.py","sha256":"858552f59d4e90dbdc9447ccc9329dd37c0a83b974f914fda2b7b7140b32dfaa"},{"bytes":23647,"git_blob":"ce1cdfc3aba5ab3e635839d3b558bbd95f4ad923","mode":"100644","path":"core/market_intelligence/market_history_export.py","sha256":"57703a286decca08700e29c6f43206403f5c955c638f5bdfab63378335c8243d"},{"bytes":20705,"git_blob":"64473d1410b795c0b7fd1a677ff67c59c72aee85","mode":"100644","path":"core/market_intelligence/market_history_operations.py","sha256":"c1ca38c72a9b439733874fb728cffa6b71a19c7ad60b00b11e1419c439705904"},{"bytes":18798,"git_blob":"eb14da8d084bceabf7a9711b5f1876d308d5e992","mode":"100644","path":"core/market_intelligence/market_input_materializer.py","sha256":"6e846956ff8fd158dc814a22aec4cf5faaad1fbe469db37d4e17649bb526723d"},{"bytes":26236,"git_blob":"552f907d8bb6c7f0a7caebc757ef5c0234109f50","mode":"100644","path":"core/market_intelligence/market_regime.py","sha256":"3f590030e2c8b39fed97fb7783c42193b757c9857e37453749c4f701cb9bc80b"},{"bytes":11694,"git_blob":"81ffc4aa27d38c7394b4b2756fc7450a91ab4c08","mode":"100644","path":"core/market_intelligence/market_research_backfill.py","sha256":"1d8b21e733717d5a1e850d2ec723479718c22e25c56f1c9a86a1d14cb4bb0a7e"},{"bytes":37058,"git_blob":"6728011c2848e4f694655fb6eb566cdaf99fb950","mode":"100644","path":"core/market_intelligence/market_snapshot.py","sha256":"f61f8f97df3a6c665928810787549ebe817082367af6ad432279147ec570817d"},{"bytes":35888,"git_blob":"c9b095e314204a009706062cc0d47672945db4c4","mode":"100644","path":"core/market_intelligence/market_store.py","sha256":"bf241133e83548d90198d57c43b823195c289f0d3287f64657dfd9460c5ad421"},{"bytes":5114,"git_blob":"bbd653b68f14cd9e488d43fc0a58fd4a1f0c1cf9","mode":"100644","path":"core/market_intelligence/melted_relationship_challenger.py","sha256":"65b43a3095ea5dffafc6ea82accf922dd7ec7f6bc618daff8d48699fd505e056"},{"bytes":10568,"git_blob":"f8d46908e2605cfcfed912f8695f7314eca2190e","mode":"100644","path":"core/market_intelligence/melted_relationships.py","sha256":"6be7ea62ecd2858ae84117a782c7cf2600adb4fef66b1a7b5c664d06445d96da"},{"bytes":7749,"git_blob":"f52a1449e0bcde3d63fcf0865f8fc72323103c16","mode":"100644","path":"core/market_intelligence/price_magnitude_policy.py","sha256":"fddd282690e956a010b80eac56161c70d8f024dabd6fdc2efc2eb657c177b5bc"},{"bytes":111954,"git_blob":"2f35c91cee6dcf0974fa862f78a799dc9fdda613","mode":"100644","path":"core/market_intelligence/private_capture.py","sha256":"1b9c611692978edec4c37de217e77abf00f5a0e456006060c936d40c53978929"},{"bytes":8029,"git_blob":"e35cf4e42c42edeff708c646e80b5bd0de341a82","mode":"100644","path":"core/market_intelligence/private_capture_service.py","sha256":"d7bc4d85b545fd2c273cfa047f0d0cc934efcefaee3b113907af937b8ee5b166"},{"bytes":56967,"git_blob":"df6b523711b747036acd00a74f37864ee2249f05","mode":"100644","path":"core/market_intelligence/private_capture_telegram.py","sha256":"75db6178b8f254ae3af5ca679d6e81dd2c3d72074fff78431e718865488aa327"},{"bytes":38160,"git_blob":"bde6cfaad335be72b607dd39ce73bf7a438d4e8f","mode":"100644","path":"core/market_intelligence/private_coin_processor.py","sha256":"c980412cdc10ab722ec22f4858c39ee4965875620dc24e0ddad2c87054d81564"},{"bytes":23789,"git_blob":"59cfadee4b70c786ff9e2db972e7a9bb89c4ec23","mode":"100644","path":"core/market_intelligence/private_gold.py","sha256":"a823923776b67ef34f61336d8c8da8c940f635f149ef1117dfc78be03292db95"},{"bytes":14024,"git_blob":"ffd2be52171736f267dd8a33f34a99437cad4fb7","mode":"100644","path":"core/market_intelligence/private_gold_payloads.py","sha256":"c91ebbbce9014670ba302a51f79f79cc085e7442d66985e56382cbced4a0913a"},{"bytes":5882,"git_blob":"a18c50887b24bf2f8853d4857dd0c2d4a977fad1","mode":"100644","path":"core/market_intelligence/private_gold_pipeline.py","sha256":"05672c198ddee5a3f3554f027597772301e8854485b84c139a7c86de2b31834a"},{"bytes":21253,"git_blob":"29fbf84ac7b29f7dcf7f1e0fabfbcc339e0f1305","mode":"100644","path":"core/market_intelligence/private_gold_staging.py","sha256":"c8ecad69d14dd85a4c0d5f4fe241ff8facc83ea275899ec20515145fdf78d4d5"},{"bytes":10740,"git_blob":"74d5d7fdf56b156c0787f7ecab9603728584f6f4","mode":"100644","path":"core/market_intelligence/private_gold_telegram.py","sha256":"638702f8ab717fe4b813b1cccc782e2a551a8f0526a18e6ff0bf97c3486d0869"},{"bytes":10182,"git_blob":"718285b3a8f009da87854ecc8c0c73b803845dbf","mode":"100644","path":"core/market_intelligence/private_gold_trade_revisions.py","sha256":"727ff3078ffe80102fc6515cf9e669034a1e07430253b0675e6198aac44b9df5"},{"bytes":10254,"git_blob":"45bc5669cf6167c2f5210cde213fa8d590e3a218","mode":"100644","path":"core/market_intelligence/private_market_transport.py","sha256":"a3c609573709a40d763cf85cdce6ef5f32d64adc2965e511633040937ecf8283"},{"bytes":28410,"git_blob":"881f1e1bca72f61b3ef4b904f898029b41496503","mode":"100644","path":"core/market_intelligence/private_pipeline_contracts.py","sha256":"b04d0625a5a4ba641b7a104450c61f769af8e3a47670705b06fcfb05274d5803"},{"bytes":37187,"git_blob":"ddbaabbf463e24291567b88f6a386da502282a2d","mode":"100644","path":"core/market_intelligence/private_pipeline_foundation.py","sha256":"f9d2b55921431e069b06e7de463d8b1d36ecbc01a94d5d0d98cd59f1dc9260da"},{"bytes":20115,"git_blob":"63dcccec01b685bd8c72af248596ee9a3109e243","mode":"100644","path":"core/market_intelligence/product_snapshot_reader.py","sha256":"b36686130c88df193040e32f7c02da1d2a8b5c639d2c93e45a0a977c64053f2a"},{"bytes":8291,"git_blob":"c44f4931cde09c10ea709abd4e1a6dd1a23abd13","mode":"100644","path":"core/market_intelligence/project_outbox.py","sha256":"ecd7c3714e653c1d6326b46c7924749317ee6f5db9a3ed1dff916815b86a91c9"},{"bytes":12884,"git_blob":"aecd6848c3824ad9255ab0f4d9b84691060ad1ab","mode":"100644","path":"core/market_intelligence/project_outbox_consumer.py","sha256":"9f04312689467e481041eea667efb883ea1b566ee097cf5b40909607d252dad7"},{"bytes":484,"git_blob":"adefbf79f86b5e4a780fdc02330902f74a42e726","mode":"100644","path":"core/market_intelligence/public_telegram/__init__.py","sha256":"710275f1238c514470e414800183e5d85c865ca60415ac232eda5f845127eb27"},{"bytes":9654,"git_blob":"9b6cceb2fdbb31622bb8ee52520922e30a6dcaee","mode":"100644","path":"core/market_intelligence/public_telegram/ingest.py","sha256":"3b1ecc8889e01a97cc3018e83d47ef869bfeeb661b3818a7811b79cbb02bb2c6"},{"bytes":9986,"git_blob":"da11356cc1583093bfeeb939fdc1097ad116b11d","mode":"100644","path":"core/market_intelligence/public_telegram/parser.py","sha256":"932ab5cd8445da63fa05a4122f10251305ca260bb186b3f836a5cf9568f4766b"},{"bytes":1598,"git_blob":"a3f4a275f066a58a435ce52c0c403e904b9a0d5c","mode":"100644","path":"core/market_intelligence/public_telegram/sources.py","sha256":"093bafdb638da92810f0d3cb66a281573576a62e356d20296967641b69a9f7cc"},{"bytes":10146,"git_blob":"04a80ffd417d0f9e96c7a092c759719a2e77e934","mode":"100644","path":"core/market_intelligence/public_telegram/transport.py","sha256":"140782341b5e0c1bfc12d687d342e567608312b171151eb9e415e7d2bcf23f0d"},{"bytes":18316,"git_blob":"2224519cb750a3af7681a4b14e5c872a797962ca","mode":"100644","path":"core/market_intelligence/relationship_ledger.py","sha256":"db4a83d3c1ad28d6c42653392f1c050962d4136e0b5c8c8350973095eea09bc6"},{"bytes":14437,"git_blob":"c68bf864fd3cee96d22054cdc5e6ac80d65ce057","mode":"100644","path":"core/market_intelligence/research_archive.py","sha256":"03ca79318e4108964c63ab20973ce9bf3bec8627a1064d06e21f7216c4320269"},{"bytes":37523,"git_blob":"fd35f86769a44582ee02749e8a9ada09bcf0f725","mode":"100644","path":"core/market_intelligence/shadow_legacy_bridge.py","sha256":"bdd48c5f4076a399d070fb39404167bceaff49a92be560a469d7592352ecf48e"},{"bytes":64986,"git_blob":"81e741f5778df537e709d32383734a356c011159","mode":"100644","path":"core/market_intelligence/shadow_parity.py","sha256":"5321eceb5e3d94b6fc9a82c84962070cbcab16d4fb84b27fd8fa75f70e77d475"},{"bytes":41376,"git_blob":"5e798c360955c0e1657de8875b2b6e10f5766e2b","mode":"100644","path":"core/market_intelligence/single_owner_parity.py","sha256":"5efae9bdc22c216f3765ee13ea5f13d273443d84b80d4417ba7d46eb801dc553"},{"bytes":6307,"git_blob":"147f653e75f942e8b3ea6fb1646cd56215bd6ccb","mode":"100644","path":"core/market_intelligence/snapshot_publisher.py","sha256":"e6e3f2d37a91994c8f42420f60c162e3f931297a8bb7506d9b69c916f94c9bad"},{"bytes":1988,"git_blob":"574bbb78739700420a850d881a7d00524d30ae4b","mode":"100644","path":"core/market_intelligence/xau_model_input.py","sha256":"9c3bde37721993441501d3025a9ca5b7cba461017bef3caf8de3cf6263794b37"},{"bytes":60,"git_blob":"e88df8f564ab57344b1a53c958f12460c953ebe5","mode":"100644","path":"deploy/market-data/migrations/0001_market_archive.down.sql","sha256":"c561b9d043e5ad76fbf9a9f89575439c6a29e6adc712f73b278fb2d927fbe8cd"},{"bytes":17402,"git_blob":"c6b12cff9399708b0996e83c9d32f016feb1d2c2","mode":"100644","path":"deploy/market-data/migrations/0001_market_archive.up.sql","sha256":"95e3ba47dee0e329b96215cdc235d7387d7d4caca4dc1df123191fe8ef0e2aeb"},{"bytes":4278,"git_blob":"bda10b7f71f636e691547623be3b6a73a9310551","mode":"100644","path":"deploy/market-data/migrations/0002_history_backfill.up.sql","sha256":"5fec573d2137e33e52d808ce982f2800a605ae1fc2e03b92ed849ef5ad59b240"},{"bytes":589,"git_blob":"5c01781a3d0547dcd2600442d9f3a9021d9a8e3b","mode":"100644","path":"deploy/market-data/migrations/0003_research_archive.down.sql","sha256":"02ce2e617ec878cb59466ebd9975b2823a3b37b5a5650ef611b002d4a0609270"},{"bytes":2463,"git_blob":"bf53c00cb92b34ccfb1fe79f58a819a59908e9c1","mode":"100644","path":"deploy/market-data/migrations/0003_research_archive.up.sql","sha256":"e6585ec4d959ba442dfabd7d09837de58fbd3adc1bc37609b21329d2204c34a9"}]
SCOPES = ('core/market_intelligence', 'contracts/market_data', 'deploy/market-data/migrations')
SINGLES = frozenset(('core/__init__.py', 'config/market_data_sources.v1.json'))
CODES = frozenset(('flags', 'manifest', 'path', 'parent', 'metadata', 'identity', 'inventory',
                   'bound', 'content', 'deadline', 'final', 'unclassified'))


class Refusal(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise Refusal(code)


def ident(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _inspect(root, rows, owner):
    """The fixed main calls /app/ROWS/10001; owned fixtures use actual owners."""
    root = Path(root)
    expected = {row['path']: row for row in rows}
    need(len(expected) == len(rows) and root.is_absolute() and root.resolve(strict=True) == root, 'path')
    end = time.monotonic() + 30
    directories = {}
    files = []
    cache = []
    found = set()
    scanned = set()
    ancestor = []
    entries_seen = 0
    primary = None
    for parent in root.parents:
        value = ident(parent.lstat())
        need(parent.resolve(strict=True) == parent and stat.S_ISDIR(value[2]), 'parent')
        ancestor.append((parent, value))

    def check(closed=False):
        need(time.monotonic() < end, 'deadline')
        need(all(path.resolve(strict=True) == path and ident(path.lstat()) == value for path, value in ancestor), 'parent')
        for path, fd, value in directories.values():
            need(path.resolve(strict=True) == path and ident(path.lstat()) == value, 'identity')
            if not closed:
                need(ident(os.fstat(fd)) == value, 'identity')
        for path, fd, value in files:
            need(path.resolve(strict=True) == path and ident(path.lstat()) == value, 'identity')
            if not closed:
                need(ident(os.fstat(fd)) == value, 'identity')
        for path, value in cache:
            need(path.resolve(strict=True) == path and ident(path.lstat()) == value, 'identity')

    def directory(name):
        need(time.monotonic() < end and len(directories) < 128, 'bound')
        if name in directories:
            return directories[name][1]
        if name:
            parent, _, leaf = name.rpartition('/')
            parent_fd = directory(parent)
            before = ident(os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False))
            need(stat.S_ISDIR(before[2]) and before[3:5] == owner and not before[2] & 0o022, 'metadata')
            fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        else:
            before = ident(root.lstat())
            need(stat.S_ISDIR(before[2]) and before[3:5] == owner and not before[2] & 0o022, 'metadata')
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        path = root / name
        try:
            need(ident(os.fstat(fd)) == before == ident(path.lstat()) and path.resolve(strict=True) == path, 'identity')
        except BaseException:
            os.close(fd)
            raise
        directories[name] = (path, fd, before)
        return fd

    def source(name):
        row = expected[name]
        parent, _, leaf = name.rpartition('/')
        parent_fd = directory(parent)
        before = ident(os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False))
        need(before[2:7] == (int(row['mode'], 8), *owner, 1, row['bytes']), 'metadata')
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent_fd)
        files.append((root / name, fd, before))
        check()
        digest = hashlib.sha256()
        count = 0
        while True:
            need(time.monotonic() < end, 'deadline')
            part = os.read(fd, min(65536, row['bytes'] - count + 1))
            if not part:
                break
            count += len(part)
            need(count <= row['bytes'], 'bound')
            digest.update(part)
        need(count == row['bytes'] and digest.hexdigest() == row['sha256'], 'content')
        check()
        found.add(name)

    def walk(name, in_cache=False):
        nonlocal entries_seen
        fd = directory(name)
        with os.scandir(fd) as iterator:
            children = []
            for item in iterator:
                entries_seen += 1
                need(entries_seen <= 1024 and len(item.name) <= 255 and item.name not in ('.', '..'), 'bound')
                children.append(item.name)
        scanned.add(name)
        for leaf in sorted(children):
            child = name + '/' + leaf
            info = ident(os.stat(leaf, dir_fd=fd, follow_symlinks=False))
            if child in expected and not in_cache:
                source(child)
            elif stat.S_ISDIR(info[2]):
                need(not in_cache and (leaf == '__pycache__' or any(path.startswith(child + '/') for path in expected)), 'inventory')
                walk(child, leaf == '__pycache__')
            else:
                need(leaf.endswith('.pyc') and stat.S_ISREG(info[2]) and info[2:6] == (0o100644, *owner, 1)
                     and 0 < info[6] <= 4 * 1024**2 and len(cache) < 512, 'inventory')
                # Cache metadata only: O_PATH, fstat, close. Never read/pread it.
                handle = os.open(leaf, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                try:
                    need(ident(os.fstat(handle)) == info == ident((root / child).lstat()), 'identity')
                finally:
                    os.close(handle)
                cache.append((root / child, info))
        check()

    try:
        directory('')
        for name in sorted(SINGLES):
            need(name in expected, 'manifest')
            source(name)
        for scope in SCOPES:
            walk(scope)
        need(found == set(expected), 'inventory')
        # Re-list each admitted directory only; no unknown child is traversed.
        for name in scanned:
            fd = directories[name][1]
            known = {path[len(name) + 1:].split('/', 1)[0] for path in expected if path.startswith(name + '/')}
            known |= {str(path.relative_to(root))[len(name) + 1:].split('/', 1)[0] for path, _ in cache if str(path.relative_to(root)).startswith(name + '/')}
            known |= {child[len(name) + 1:].split('/', 1)[0] for child in directories if child.startswith(name + '/')}
            with os.scandir(fd) as iterator:
                observed = []
                for item in iterator:
                    need(len(observed) < 1024, 'bound')
                    observed.append(item.name)
            need(set(observed) == known, 'inventory')
        check()
    except BaseException as error:
        primary = error
    finally:
        errors = []
        try:
            check()
        except BaseException as error:
            errors.append(error)
        for _, fd, _ in reversed(files):
            try:
                os.close(fd)
            except BaseException as error:
                errors.append(error)
        for _, fd, _ in reversed(list(directories.values())):
            try:
                os.close(fd)
            except BaseException as error:
                errors.append(error)
        try:
            check(closed=True)
        except BaseException as error:
            errors.append(error)
        if primary is not None:
            raise primary
        if errors:
            raise Refusal('final') from None
    return {'source_files': len(found), 'excluded_cache_metadata_files': len(cache)}


def main():
    need(sys.flags.isolated == 1 and sys.flags.no_site == 1 and sys.flags.dont_write_bytecode == 1, 'flags')
    need(len(ROWS) == 93 and hashlib.sha256(encoded(ROWS)).hexdigest() == ROWS_SHA, 'manifest')
    value = _inspect(Path('/app'), ROWS, (10001, 10001))
    return {'schema': 'market_public_source93_readonly_observation/1', 'status': 'match',
            'expected_manifest_sha256': MANIFEST_SHA, 'expected_rows_sha256': ROWS_SHA,
            **value, 'all_source_rows_match': True, 'project_cache_bodies_read': False,
            'unknown_noncache_files_admitted': False, 'project_imported': False,
            'source_only_nonatomic_observation': True, 'already_running_code_proven': False,
            'business_data_freshness_proven': False, 'security_accepted': False}


if __name__ == '__main__':
    try:
        result = main()
        exit_code = 0
    except BaseException as error:
        code = error.args[0] if type(error) is Refusal and len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in CODES else 'unclassified'
        result = {'schema': 'market_public_source93_readonly_observation/1', 'status': 'refused',
                  'code': code, 'all_source_rows_match': False, 'project_cache_bodies_read': False,
                  'project_imported': False, 'security_accepted': False}
        exit_code = 2
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(exit_code)
