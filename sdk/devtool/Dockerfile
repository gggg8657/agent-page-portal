# 개발자 업로드 도구의 바탕 이미지 dongjukim-devcheck:<판> — 격리 자가검증(devsubmit.sandbox)과 승인된 도구 실행(fastportal/devtool.py)이 같이 씀.
# 파이썬 + 시작 프로그램(entry.py: LLM·임베딩 중계 + python app.py)만. 바깥에서 받는 것은 고정 digest 의 파이썬 이미지 하나뿐(폐쇄망: docker save/load).
#   만들기: bash scripts/build-devcheck-image.sh   (도구 이미지는 승인 때 FROM 이 이미지 + /app 복사 + requirements.txt 바퀴(wheel)만 설치)
ARG PY_IMAGE=python:3.11-slim-trixie@sha256:0a236bf8f0b6013383df04d1cd02cb0abe5fbfa1a7012141d7ff9202b7cb30a6
FROM ${PY_IMAGE}
COPY entry.py /opt/devtool/entry.py
RUN chmod 755 /opt/devtool && chmod 644 /opt/devtool/entry.py && mkdir -p /app /data && chmod 755 /app /data
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 HOME=/tmp
LABEL dongjukim.devcheck=base
USER 10001:10001
WORKDIR /app
ENTRYPOINT ["python", "/opt/devtool/entry.py"]
