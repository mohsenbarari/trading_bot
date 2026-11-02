FROM python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3
RUN python -m pip install --no-cache-dir coverage==7.15.0 PyYAML==6.0.3 Jinja2==3.1.6
COPY source/ /reviewed/
COPY expectation.json /expectation.json
RUN test ! -e /reviewed/.env
WORKDIR /reviewed
ENTRYPOINT ["python3","-I","-B","-m","coverage","run","--branch","--rcfile=/reviewed/.coveragerc","--data-file=/work/.coverage.root-fixtures","/reviewed/scripts/run_coverage_unittest.py","--lane","root","--expectation","/expectation.json"]
