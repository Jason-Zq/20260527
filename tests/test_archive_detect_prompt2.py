"""提示词库 / 批次总体判定2 纯函数测试(不依赖 DB / LLM)。

- normalize_prompt_key 五字段归一化(None→'' + strip)
- render_judge_overall_template:token 渲染 / 缺 token 兜底追加 / JSON 花括号安全 / 注入值不二次扫描
- render(默认模板) 与重构前 _build_judge_batch_overall_prompt 输出逐字一致(钉住判定1 不漂移)
- _parse_overall_json:verdict 白名单 + score 钳位/档位归一
- judge_batch_overall_v2 / generate_archive_prompt_standard 的 operation 接线(mock _call_llm)

运行: PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv312/Scripts/python.exe tests/test_archive_detect_prompt2.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import llm_service
from db import prompt_library_crud as plc

# 重构前 _build_judge_batch_overall_prompt 的黄金输出(2026-08-06 捕获),钉住判定1 模板不漂移
_GOLDEN_FULL = '你是文档留底审核的总体判定助手。请综合一个批次内所有文件的检测结果,对**整个批次是否满足该进展的留底要求**给出总体判断。\n\n本进展客户姓名：张三；办理人：李四。\n用户判定标准(该进展要求):\n标准X\n\n当前审核阶段:递交后(post_submit)——关注证明已递交/已受理的官方回执类材料。\n\n各文件检测明细(共 1 条):\n- 文件1 「a.pdf」: verdict=match, score=90, 类别=G-批复函, reason=理由一, 要点=k1 | k2\n\n判定原则(重要):\n0. **本次总体判定只关注批次内文件是否与公司留底分类体系相关,不核对具体项目名称、投资金额、转账金额等细节。**留底的核心是证明相关服务确已启动或发生,而不是必须有官方回执类关键件。证据既可以是官方文件(批复函/受理回执/递交确认),**也可以是软证据的组合**:如服务合同 + 聊天记录/邮件/确认截图(客户确认凭证)。合同 + 聊天记录/确认截图**不是无关附件**,它们组合起来即可构成证明服务已启动的关键证据链。\n1. 一个批次通常包含**关键证据**(能证明服务成立,含上述官方件或软证据组合)和**附带文件**(身份证、护照等辅助材料)。\n2. **只要存在能证明服务已启动/已发生的证据(官方件或软证据组合任一即可),整批即判为 match**,不因附带文件与进展不完全相关而降级——附带文件的低分不应拉低整体结论。\n3. 若只有辅助性附件、既无官方关键件、也无任何能证明服务已启动的软证据组合,则判 partial(材料不齐)。\n4. 完全没有任何文件能体现服务已启动或发生 → mismatch。\n5. **阶段错配不作硬性否决**:文件更适用于其他阶段(如递交前材料出现在递交后批次)不影响整体符合性,只要文件本身可归入分类体系且与客户/办理人相关即可。\n6. **单份文件检测失败/加密无法读取,只影响该文件,不影响其他文件的整体判断**;不要因个别文件读取异常就把整批判为 mismatch。\n\n请输出严格 JSON(不要 markdown、不要多余文字):\n{"verdict":"match|partial|mismatch","score":0-100整数,"reason":"80-200字中文总体说明"}\nscore 与 verdict 需一致:match≥80,partial 50-79,mismatch<50。\nreason 需说明:整体结论依据、关键文件命中情况、缺失或问题点。\n**脱敏**:不要泄露金额/电话/身份证号/银行卡号,用 [金额]/[手机号]/[身份证]/[银行卡] 占位。\n'
_GOLDEN_EMPTY = '你是文档留底审核的总体判定助手。请综合一个批次内所有文件的检测结果,对**整个批次是否满足该进展的留底要求**给出总体判断。\n\n用户判定标准(该进展要求):\n标准Y\n\n各文件检测明细(共 0 条):\n（无符合 done 状态的文件）\n\n判定原则(重要):\n0. **本次总体判定只关注批次内文件是否与公司留底分类体系相关,不核对具体项目名称、投资金额、转账金额等细节。**留底的核心是证明相关服务确已启动或发生,而不是必须有官方回执类关键件。证据既可以是官方文件(批复函/受理回执/递交确认),**也可以是软证据的组合**:如服务合同 + 聊天记录/邮件/确认截图(客户确认凭证)。合同 + 聊天记录/确认截图**不是无关附件**,它们组合起来即可构成证明服务已启动的关键证据链。\n1. 一个批次通常包含**关键证据**(能证明服务成立,含上述官方件或软证据组合)和**附带文件**(身份证、护照等辅助材料)。\n2. **只要存在能证明服务已启动/已发生的证据(官方件或软证据组合任一即可),整批即判为 match**,不因附带文件与进展不完全相关而降级——附带文件的低分不应拉低整体结论。\n3. 若只有辅助性附件、既无官方关键件、也无任何能证明服务已启动的软证据组合,则判 partial(材料不齐)。\n4. 完全没有任何文件能体现服务已启动或发生 → mismatch。\n5. **阶段错配不作硬性否决**:文件更适用于其他阶段(如递交前材料出现在递交后批次)不影响整体符合性,只要文件本身可归入分类体系且与客户/办理人相关即可。\n6. **单份文件检测失败/加密无法读取,只影响该文件,不影响其他文件的整体判断**;不要因个别文件读取异常就把整批判为 mismatch。\n\n请输出严格 JSON(不要 markdown、不要多余文字):\n{"verdict":"match|partial|mismatch","score":0-100整数,"reason":"80-200字中文总体说明"}\nscore 与 verdict 需一致:match≥80,partial 50-79,mismatch<50。\nreason 需说明:整体结论依据、关键文件命中情况、缺失或问题点。\n**脱敏**:不要泄露金额/电话/身份证号/银行卡号,用 [金额]/[手机号]/[身份证]/[银行卡] 占位。\n'

_FILES_1 = [{"filename": "a.pdf", "verdict": "match", "match_score": 90,
             "doc_category": "G-批复函", "reason": "理由一", "key_points": ["k1", "k2"]}]


def test_normalize_prompt_key():
    assert plc.normalize_prompt_key(None, None, None, None, None) == ("", "", "", "", "")
    assert plc.normalize_prompt_key("  项目A ", " C1 ", None, "D2", " 进展X ") == ("项目A", "C1", "", "D2", "进展X")
    print("  OK   test_normalize_prompt_key")


def test_render_golden_full():
    out = llm_service.render_judge_overall_template(
        llm_service.DEFAULT_JUDGE_OVERALL_TEMPLATE,
        user_prompt="标准X", files_brief=_FILES_1,
        stage="post_submit", client_name="张三", handler="李四")
    assert out == _GOLDEN_FULL
    # 判定1 入口同样逐字一致
    assert llm_service._build_judge_batch_overall_prompt(
        _FILES_1, "标准X", stage="post_submit", client_name="张三", handler="李四") == _GOLDEN_FULL
    print("  OK   test_render_golden_full")


def test_render_golden_empty():
    out = llm_service.render_judge_overall_template(
        llm_service.DEFAULT_JUDGE_OVERALL_TEMPLATE,
        user_prompt="标准Y", files_brief=[])
    assert out == _GOLDEN_EMPTY
    print("  OK   test_render_golden_empty")


def test_render_empty_template_falls_back_to_default():
    for tpl in (None, "", "   "):
        out = llm_service.render_judge_overall_template(
            tpl, user_prompt="标准X", files_brief=_FILES_1,
            stage="post_submit", client_name="张三", handler="李四")
        assert out == _GOLDEN_FULL
    print("  OK   test_render_empty_template_falls_back_to_default")


def test_render_missing_tokens_fallback_appends():
    tpl = "自定义模板正文,只含判定原则。"
    out = llm_service.render_judge_overall_template(
        tpl, user_prompt="标准Z",
        files_brief=[{"filename": "b.pdf", "verdict": "partial", "match_score": 60,
                      "doc_category": "A", "reason": "r", "key_points": []}])
    assert out.startswith("自定义模板正文")
    assert "\n用户判定标准(该进展要求):\n标准Z" in out
    assert "各文件检测明细(共 1 条):" in out and "b.pdf" in out
    print("  OK   test_render_missing_tokens_fallback_appends")


def test_render_json_braces_safe():
    # 模板含 JSON 花括号字面量:.format 会炸,单遍正则替换必须安全
    tpl = '头部 {"verdict":"match"} {user_prompt} 尾部'
    out = llm_service.render_judge_overall_template(tpl, user_prompt="标准Q", files_brief=[])
    assert '{"verdict":"match"}' in out and "标准Q" in out
    print("  OK   test_render_json_braces_safe")


def test_render_injected_token_text_not_rescanned():
    # 注入值恰好含 token 文本:单遍替换不二次扫描,原样保留
    out = llm_service.render_judge_overall_template(
        "{user_prompt}", user_prompt="{files_detail}", files_brief=[])
    assert "{files_detail}" in out
    print("  OK   test_render_injected_token_text_not_rescanned")


def test_parse_overall_json_bands():
    assert llm_service._parse_overall_json('{"verdict":"match","score":55,"reason":"r"}')["score"] == 80
    assert llm_service._parse_overall_json('{"verdict":"partial","score":90,"reason":"r"}')["score"] == 65
    assert llm_service._parse_overall_json('{"verdict":"partial","score":20,"reason":"r"}')["score"] == 65
    assert llm_service._parse_overall_json('{"verdict":"mismatch","score":70,"reason":"r"}')["score"] == 49
    # 前后噪包容错 + 钳位
    assert llm_service._parse_overall_json('前言 {"verdict":"match","score":92,"reason":"r"} 后记')["score"] == 92
    assert llm_service._parse_overall_json('{"verdict":"match","score":120,"reason":"r"}')["score"] == 100
    r = llm_service._parse_overall_json('{"verdict":"mismatch","score":-5,"reason":"  理由  "}')
    assert r["score"] == 0 and r["reason"] == "理由"
    print("  OK   test_parse_overall_json_bands")


def test_parse_overall_json_rejects():
    for raw in ("不是JSON", '{"verdict":"unknown","score":1}', '{"verdict":"match","score":"abc"}', ""):
        try:
            llm_service._parse_overall_json(raw)
        except ValueError:
            pass
        else:
            raise AssertionError("应抛 ValueError: %r" % raw)
    print("  OK   test_parse_overall_json_rejects")


def test_build_retention_standard_prompt():
    p = llm_service.build_retention_standard_prompt("项目A", "C1", None, "", "进展X")
    assert "项目名称：项目A" in p and "项目编码：C1" in p and "进展名称：进展X" in p
    assert "项目详情：" not in p  # 空字段跳过
    print("  OK   test_build_retention_standard_prompt")


def test_judge_v2_operation_wiring():
    captured = {}
    orig = llm_service._call_llm

    def fake(prompt, operation=None, **ctx):
        captured["prompt"] = prompt
        captured["operation"] = operation
        return '{"verdict":"match","score":88,"reason":"ok"}'

    llm_service._call_llm = fake
    try:
        r = llm_service.judge_batch_overall_v2(
            _FILES_1, "项目标准条文", judge_template="TPL {user_prompt} {files_detail}")
    finally:
        llm_service._call_llm = orig
    assert captured["operation"] == "judge_batch_overall_2"
    assert captured["prompt"].startswith("TPL ") and "项目标准条文" in captured["prompt"] and "a.pdf" in captured["prompt"]
    assert r == {"verdict": "match", "score": 88, "reason": "ok"}
    print("  OK   test_judge_v2_operation_wiring")


def test_generate_standard_operation_and_empty_reject():
    captured = {}
    orig = llm_service._call_llm
    llm_service._call_llm = lambda prompt, operation=None, **ctx: (captured.update(operation=operation) or "  标准条文  ")
    try:
        text = llm_service.generate_archive_prompt_standard("项目A", None, None, None, "进展X")
    finally:
        llm_service._call_llm = orig
    assert captured["operation"] == "generate_archive_prompt2"
    assert text == "标准条文"

    llm_service._call_llm = lambda *a, **k: "   "
    try:
        try:
            llm_service.generate_archive_prompt_standard("项目A")
        except ValueError:
            pass
        else:
            raise AssertionError("空响应应抛 ValueError")
    finally:
        llm_service._call_llm = orig
    print("  OK   test_generate_standard_operation_and_empty_reject")


if __name__ == "__main__":
    test_normalize_prompt_key()
    test_render_golden_full()
    test_render_golden_empty()
    test_render_empty_template_falls_back_to_default()
    test_render_missing_tokens_fallback_appends()
    test_render_json_braces_safe()
    test_render_injected_token_text_not_rescanned()
    test_parse_overall_json_bands()
    test_parse_overall_json_rejects()
    test_build_retention_standard_prompt()
    test_judge_v2_operation_wiring()
    test_generate_standard_operation_and_empty_reject()
    print("All tests passed.")
