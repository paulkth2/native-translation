#!/usr/bin/env python3
"""
demo_make_outputs.py — generate two benchmark output files for the method-lift demo.

These translations were produced by Claude Opus 4.8 in two modes:
  NAIVE     : a default "translate this to Korean" pass (no scaffold) — realistic literal output.
  SCAFFOLD  : the korean-product-translation skill + two-pass native polish.

This is a SINGLE-MODEL method-lift demo (not the multi-model bake-off, which needs API keys).
It writes records in run.py's output format so score.py / visualize.py can consume them.
"""
import json
import pathlib

TESTSET = pathlib.Path(__file__).resolve().parents[1] / "testset" / "en_ko_ui.jsonl"
OUT = pathlib.Path(__file__).resolve().parent / "out"

NAIVE = {
    "btn-001": "저장",
    "btn-002": "로그인",
    "btn-003": "계정 삭제하기",
    "btn-004": "공유하기",
    "btn-005": "업데이트",
    "err-001": "파일이 열리지 못했습니다.",
    "err-002": "변경사항들을 저장하는 데 실패했습니다.",
    "err-003": "하나의 오류가 파일 업로드 중에 발생했습니다.",
    "err-004": "이 행동은 취소되어질 수 없습니다.",
    "err-005": "당신은 이 파일에 접근할 권한을 가지고 있지 않습니다.",
    "err-var-001": "{server}에 연결할 수 없었습니다.",
    "cfm-001": "당신은 정말로 이것을 삭제하기를 원하십니까?",
    "cfm-002": "계속하기를 원하십니까?",
    "cfm-003": "저장되지 않은 변경사항들을 폐기하시겠습니까?",
    "josa-001": "{fileName}이 삭제되었습니다.",
    "josa-002": "{folder}으로 이동",
    "josa-003": "계속하려면 {key}를 누르세요",
    "josa-004": "{user}가 당신의 게시물에 댓글을 달았습니다.",
    "cnt-001": "당신은 {count}개의 새로운 메시지들을 가지고 있습니다.",
    "cnt-002": "{count}개의 아이템들이 선택되었습니다",
    "cnt-003": "{count, plural, one {# 파일} other {# 파일들}}",
    "cnt-004": "{count}명의 사람들이 입력하고 있습니다",
    "hon-001": "당신의 비밀번호를 입력해 주세요.",
    "hon-002": "당신의 변경사항들이 저장되었습니다.",
    "hon-003": "우리는 당신의 이메일로 인증 코드를 보냈습니다.",
    "gls-001": "앱 스토어에서 열기",
    "gls-002": "당신의 워크스페이스를 동기화하세요",
    "gls-003": "하나의 요금제를 선택하세요",
    "ph-001": "환영합니다, {name}!",
    "ph-002": "마지막으로 업데이트됨: {date}",
    "ph-003": "한 달에 {price}",
    "sts-001": "로딩 중...",
    "sts-002": "저장 중...",
    "sts-003": "결과들을 찾을 수 없습니다.",
    "sts-004": "활성화됨",
    "tns-001": "저장 그리고 종료",
    "tns-002": "이 요금제는 5명의 멤버들을 가지고 있습니다.",
    "tns-003": "누군가 당신을 멘션할 때 알림을 받으세요.",
    "seg-001": "만약 당신이 24시간 이내에 당신의 이메일을 인증하지 않으면, 당신의 계정은 비활성화될 것이고 당신은 다시 가입해야 할 것입니다.",
    "seg-002": "알림들이 어떻게 전달되는지 변경하려면, 설정으로 가서 그리고 알림을 선택하세요.",
    "ntf-001": "{name}와 {count}명의 다른 사람들이 당신의 사진을 좋아했습니다.",
}

SCAFFOLD = {
    "btn-001": "저장",
    "btn-002": "로그인",
    "btn-003": "계정 삭제",
    "btn-004": "공유",
    "btn-005": "업데이트",
    "err-001": "파일을 열 수 없습니다.",
    "err-002": "변경사항을 저장하지 못했습니다.",
    "err-003": "파일을 업로드하는 동안 오류가 발생했습니다.",
    "err-004": "이 작업은 되돌릴 수 없습니다.",
    "err-005": "이 파일에 접근할 권한이 없습니다.",
    "err-var-001": "{server}에 연결할 수 없습니다.",
    "cfm-001": "정말 삭제하시겠어요?",
    "cfm-002": "계속하시겠어요?",
    "cfm-003": "저장하지 않은 변경사항을 삭제할까요?",
    "josa-001": "{fileName}을(를) 삭제했습니다.",
    "josa-002": "{folder}(으)로 이동",
    "josa-003": "계속하려면 {key}을(를) 누르세요",
    "josa-004": "{user}님이 게시물에 댓글을 남겼습니다.",
    "cnt-001": "새 메시지가 {count}개 있습니다.",
    "cnt-002": "{count}개 선택됨",
    "cnt-003": "{count, plural, other {파일 #개}}",
    "cnt-004": "{count}명이 입력 중",
    "hon-001": "비밀번호를 입력하세요.",
    "hon-002": "변경사항이 저장되었습니다.",
    "hon-003": "이메일로 인증 코드를 보냈습니다.",
    "gls-001": "App Store에서 열기",
    "gls-002": "워크스페이스 동기화",
    "gls-003": "요금제를 선택하세요.",
    "ph-001": "{name}님, 환영합니다.",
    "ph-002": "최종 업데이트: {date}",
    "ph-003": "월 {price}",
    "sts-001": "불러오는 중…",
    "sts-002": "저장하는 중…",
    "sts-003": "검색 결과가 없습니다.",
    "sts-004": "사용",
    "tns-001": "저장 후 종료",
    "tns-002": "이 요금제에는 멤버가 5명 있습니다.",
    "tns-003": "누군가 회원님을 멘션하면 알림을 받습니다.",
    "seg-001": "24시간 안에 이메일을 인증하지 않으면 계정이 비활성화됩니다. 이 경우 다시 회원가입해야 합니다.",
    "seg-002": "알림 전달 방식을 바꾸려면 설정으로 이동해 알림을 선택하세요.",
    "ntf-001": "{name}님 외 {count}명이 회원님의 사진을 좋아합니다.",
}


def build(mode_name, table, two_pass, naive):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"claude_{mode_name}.jsonl"
    n = 0
    with open(path, "w", encoding="utf-8") as f, open(TESTSET, encoding="utf-8") as ts:
        for line in ts:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            rec = {
                "id": e["id"], "model": f"claude-opus-4-8:{mode_name}",
                "two_pass": two_pass, "naive": naive, "category": e.get("category"),
                "register": e.get("register"), "source_en": e["source_en"],
                "placeholders": e.get("placeholders", []), "glossary": e.get("glossary", {}),
                "reference_ko": e.get("reference_ko", ""),
                "translation": table.get(e["id"], ""),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    print(f"wrote {n} rows -> {path}")


if __name__ == "__main__":
    build("naive", NAIVE, two_pass=False, naive=True)
    build("scaffold", SCAFFOLD, two_pass=True, naive=False)
