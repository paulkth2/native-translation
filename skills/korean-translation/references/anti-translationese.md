# Anti-번역투 (translationese) rules

Translationese is English structure leaking into Korean. These seven phenomena cause most of it. Each has: the problem, a **verbatim Korean instruction** you can paste into a system/polish prompt, and before→after pairs.

> Evidence note: the polish-pass framing and *itemized* rules are what work. A generic "translate naturally" instruction is unreliable and can *increase* translationese (arXiv 2503.04369). Use these concrete rules, not the adjective "natural."

---

## 1. Passive & inanimate-subject → active

English passives and "by X" agents calque badly. Korean prefers active voice and resists inanimate subjects. The double-passive `~되어지다 / ~보여지다 / ~쓰여지다` is outright ungrammatical.

**Instruction:** `영어 수동태는 기본적으로 능동태로 바꾼다. '~에 의해 ~되다', 무생물 주어 구문, 이중피동('~되어지다','~보여지다')을 피하고 행위자를 주어로 삼는다. 문맥상 꼭 필요할 때만 피동을 쓴다.`

| English | ❌ | ✅ |
|---|---|---|
| The file was deleted by the system. | 파일이 시스템에 의해 삭제되었다. | 시스템이 파일을 삭제했습니다. |
| Settings are saved. | 설정이 저장되어집니다. | 설정이 저장됩니다. |
| An error is shown. | 오류가 보여집니다. | 오류가 표시됩니다. |

## 2. Pronouns & subjects → drop (pro-drop)

Korean omits subjects/objects recoverable from context. "you/your/we/it/they" usually vanish. Never render "you/your" as 당신/너 in UI; drop it, or use 사용자/회원님 only to disambiguate.

**Instruction:** `영어의 인칭대명사(you/your/we/it/they)와 자명한 주어는 생략한다. 'you/your'는 '당신/너'로 직역하지 말고 생략하거나 꼭 필요할 때만 '사용자'로 옮긴다.`

| English | ❌ | ✅ |
|---|---|---|
| your cluster | 당신의 클러스터 | 클러스터 |
| Enter your password | 당신의 비밀번호를 입력하세요 | 비밀번호를 입력하세요 |
| We recommend enabling it. | 우리는 그것을 활성화하는 것을 권장합니다. | 활성화하는 것이 좋습니다. |

## 3. Plural 들 → drop when redundant

Korean does not obligatorily mark plurals. Drop 들 when a quantifier or context already conveys plurality. Also kill calqued `~의 그것(들)`.

**Instruction:** `복수 표지 '들'은 수량사나 문맥으로 복수가 분명하면 붙이지 않는다. 영어 복수형을 기계적으로 '들'로 옮기지 말 것.`

| English | ❌ | ✅ |
|---|---|---|
| 3 files | 3개의 파일들 | 파일 3개 |
| Delete selected items | 선택된 아이템들을 삭제 | 선택한 항목 삭제 |
| Some settings | 몇몇 설정들 | 일부 설정 |

## 4. "have" → 있다, not 가지다

`A가 B를 가지다` is the classic possessive calque. Use existence: `A에/에게 B가 있다`.

**Instruction:** `'have'를 '가지다/가지고 있다'로 직역하지 말 것. 'A가 B를 가지다'는 'A에/에게 B가 있다'로, 부정형은 'A에/에게 B가 없다'로 바꾼다.`

| English | ❌ | ✅ |
|---|---|---|
| You have 3 new messages. | 당신은 3개의 새 메시지들을 가지고 있습니다. | 새 메시지가 3개 있습니다. |
| This plan has 5 seats. | 이 요금제는 5개의 좌석을 가집니다. | 이 요금제에는 좌석이 5개 있습니다. |

## 5. Article / quantifier literalism → drop

"a/an/one" rarely needs 하나의/한 개의.

**Instruction:** `영어 부정관사 a/an은 보통 옮기지 않는다. '하나의/한 개의'는 수량이 핵심일 때만 쓴다.`

| English | ❌ | ✅ |
|---|---|---|
| Select a file | 하나의 파일을 선택하세요 | 파일을 선택하세요 |
| This is an error. | 이것은 하나의 오류입니다. | 오류가 발생했습니다. |

## 6. Connectives → endings, not 그리고/그러나/그래서

English coordinators map to Korean **verb endings**, not repeated sentence-initial adverbs. The list calque "A, B 그리고 C" is a clear tell.

**Instruction:** `and/but/so는 문장 첫머리 '그리고/그러나/그래서'를 반복하지 말고 연결어미(-고, -지만, -니까, -아서/어서)로 이어라. 'A, B 그리고 C' 나열은 'A, B, C'로 쓴다.`

| English | ❌ | ✅ |
|---|---|---|
| Save and close | 저장 그리고 닫기 | 저장하고 닫기 (or 저장 후 닫기) |
| It's fast, but it uses more memory. | 그것은 빠릅니다. 그러나 더 많은 메모리를 사용합니다. | 빠르지만 메모리를 더 많이 사용합니다. |
| red, green, and blue | 빨강, 초록 그리고 파랑 | 빨강, 초록, 파랑 |

## 7. Re-segment long sentences

Don't preserve English sentence boundaries 1:1. Relative-clause pile-ups (만연체) read as translated.

**Instruction:** `원문이 길거나 관계절이 중첩되면 한 문장에 욱여넣지 말고 2~3개의 짧은 한국어 문장으로 나눠라. 영어 문장 수에 1:1로 맞추지 말 것.`

| English | ❌ | ✅ |
|---|---|---|
| If you want to change the settings that control how notifications are delivered, go to the menu. | 알림이 전달되는 방식을 제어하는 설정을 변경하기를 원한다면 메뉴로 가세요. | 알림 전달 방식을 바꾸려면 메뉴로 이동하세요. 여기에서 관련 설정을 변경할 수 있습니다. |

---

## Polish-prompt template (Pass 2)

```
다음 한국어 번역을 한국어 원어민 제품 작가가 처음부터 쓴 것처럼 자연스럽게 다듬어라.
규칙:
1. 능동태 우선; '~에 의해', 무생물 주어, 이중피동('되어지다','보여지다') 제거
2. 자명한 대명사·주어 생략; 'you/your'를 '당신/너'로 옮기지 말 것
3. 불필요한 복수 '들' 제거
4. 'have'는 '가지다'가 아니라 '~에/에게 ~이 있다'
5. 불필요한 '하나의' 제거
6. 문장 첫머리 '그리고/그러나/그래서' 반복 금지 → 연결어미(-고/-지만/-니까) 사용
7. 긴 문장은 2~3개로 분할
문체: [합쇼체 ~합니다 / 해요체 ~해요 중 택1] 로 통일.
플레이스홀더({...}, %s, <태그>)와 영문 고유명사는 그대로 둔다.
의미는 절대 바꾸지 말 것. 다듬은 결과만 출력하라.

원문(영어): {source}
초벌 번역(한국어): {draft}
```
