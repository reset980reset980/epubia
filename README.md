# 혜경 전자책 스튜디오

PDF, TXT, Markdown 원고를 업로드해 한국어 EPUB/PDF/Markdown 출판 파일로 변환하는 Flask 웹앱입니다.

## 기능

- 기존 YouTube Shorts 사용자 계정 재사용
- PDF/TXT/Markdown 업로드
- 한국어 텍스트 추출 및 챕터 자동 분리
- EPUB 3 패키징
- 한글 폰트를 사용하는 PDF 생성
- 도움말 및 버전/설정 화면

## 실행

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
PORT=5010 ./venv/bin/python app.py
```

## 배포

기본 포트는 `5010`이며 Caddy에서 `epub.xsw.kr`을 이 포트로 프록시합니다.

