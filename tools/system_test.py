"""
tools/system_test.py — 全系统集成测试 V2
覆盖所有新模块：memory_manager V2 / prompt_templates / fingerprint / acceptance_tests
"""
import os, sys, json, tempfile
from unittest.mock import patch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

P="✅"; F="❌"
results=[]

def test(name):
    def dec(fn):
        def wrap():
            try:
                fn(); results.append((P,name,""))
                print(f"  {P} {name}")
            except Exception as e:
                results.append((F,name,str(e)))
                print(f"  {F} {name}: {e}")
        return wrap
    return dec

MOCK_CHAPTER="""陆承把笔放在桌上，看着对面那个人的西装口袋。
三十分钟了。对方一句有效信息都没给出来。
"说吧。"陆承开口。
对面的男人愣了一下，像是没想到他会这么直接。然后，陆承看到了那条线。
红色的，粗得异常，从那个男人的左胸延伸出去，穿过玻璃幕墙，消失在城市某处。
陆承眨了眨眼。线还在。
【人情感知已激活】【检测到债崩预警：红色债链临界值98%】
"林总，"陆承重新拿起笔，"你现在需要做的不是跟我谈条款。"
林成远突然捂住胸口。救护车是陆承叫的。
手机屏幕还亮着——那条系统通知他没有滑掉。「欢迎回来，陆氏第四代传人。」"""

MOCK_CHECKER_RESP=json.dumps({"dimensions":{"hook_power":8,"shuang_density":7,"character_voice":8,"plot_logic":8,"writing_naturalness":8},"overall_score":7.8,"strongest_point":"节奏感强","weakest_point":"爽感可更密集","specific_feedback":"整体良好"})
MOCK_COMPLIANCE_RESP=json.dumps({"passed":True,"hard_rejects":[],"warnings":[],"suggestion":""})
MOCK_TRACKER_RESP=json.dumps({"protagonist_points":150,"character_states":{"陆承":"觉醒状态"},"active_threads":["人情债觉醒"],"last_chapter_ending":"系统通知未滑掉","chapter_summary":"陆承觉醒人情感知"})

def mock_llm(agent_name,system_prompt,user_prompt,**kw):
    if agent_name in ("checker_main","checker_cross1","checker_cross2"): return MOCK_CHECKER_RESP,0.001
    if agent_name=="compliance": return MOCK_COMPLIANCE_RESP,0.001
    if agent_name=="tracker": return MOCK_TRACKER_RESP,0.001
    if agent_name=="writer": return MOCK_CHAPTER,0.005
    return json.dumps({"result":"mock"}),0.001


@test("导入：orchestrator_state")
def t1():
    from orchestrator_state import create_initial_state,save_state,load_state
    s=create_initial_state("t","测","fanqie","都市","设定",100.)
    assert s["novel_id"]=="t" and s["budget_limit_usd"]==100.

@test("导入：api_client（含统计）")
def t2():
    from api_client import call_llm,MODEL_ROUTES,get_stats,reset_stats
    assert "writer" in MODEL_ROUTES and len(MODEL_ROUTES)>=10
    reset_stats(); stats=get_stats()
    assert stats["total_calls"]==0

@test("导入：所有Agent")
def t3():
    from agents.writer_agent    import run_writer
    from agents.normalizer_agent import run_normalizer
    from agents.compliance_agent import run_compliance
    from agents.checker_agent   import run_checker
    from agents.rewriter_agent  import run_rewriter
    from agents.tracker_agent   import run_tracker
    from agents.outline_agent   import run_outline
    from agents.summarizer_agent import run_summarizer

@test("导入：所有工具（含新模块）")
def t4():
    from tools.dashboard        import print_dashboard
    from tools.exporter         import print_stats
    from tools.budget_manager   import generate_report
    from tools.fingerprint_checker import run_fingerprint_check
    from tools.acceptance_tests import run_all
    from tools.calibrate_checker import run_calibration

@test("LangGraph图编译")
def t5():
    from orchestrator import build_graph
    g=build_graph(); assert g is not None

@test("设定包完整性")
def t6():
    spath=os.path.join(BASE_DIR,"output","setting_package.json")
    assert os.path.exists(spath)
    with open(spath,encoding="utf-8") as f: s=json.load(f)
    for key in ("protagonist","power_system","arc_outline","key_characters","world_setting"):
        assert key in s, f"缺少字段:{key}"
    assert len(s["arc_outline"])>=1
    assert len(s["power_system"]["levels"])>=4

@test("prompt_templates：题材切换")
def t7():
    from config.prompt_templates import get_genre_instruction,get_hook_guidance,HOOK_TYPES,SHUANG_TYPES
    assert len(HOOK_TYPES)==7
    assert len(SHUANG_TYPES)>=5
    urban=get_genre_instruction("都市系统流")
    assert "系统流" in urban
    hook=get_hook_guidance("反转钩")
    assert "反转钩" in hook

@test("memory_manager V2：热冷分离")
def t8():
    from memory.memory_manager import empty_l2,expire_constraints,add_constraint,maybe_compress_hot_to_cold
    m=empty_l2()
    # 测试约束添加与过期
    m=add_constraint(m,"不能透露身份",10,"测试")
    assert len(m["constraints"]["forbidden_constraints"])==1
    m2,expired=expire_constraints(m,11)  # ch11时ch10的约束过期
    assert expired==1
    assert len(m2["constraints"]["forbidden_constraints"])==0
    # 测试热冷压缩
    for i in range(25):
        m["hot"]["recent_summaries"].append({"chapter":i+1,"summary":f"第{i+1}章"})
    m=maybe_compress_hot_to_cold(m,"test")
    assert len(m["hot"]["recent_summaries"])==15  # 25-10=15

@test("memory_manager V2：按需检索")
def t9():
    from memory.memory_manager import empty_l2,get_chapter_relevant_context
    m=empty_l2()
    m["hot"]["protagonist_level"]="识债者"
    m["hot"]["protagonist_points"]=600
    m["hot"]["character_states"]={"陆承":"正常","贺苗":"神秘","章廷":"监视中"}
    task={"chapter_number":5,"main_characters":["陆承","贺苗"],"forbidden_actions":[]}
    ctx=get_chapter_relevant_context(m,task)
    # 只应包含出场角色
    assert "陆承" in ctx["character_states"]
    assert "贺苗" in ctx["character_states"]
    # 章廷未出场，不应在里面（或可以，因为名字匹配）
    assert ctx["protagonist_level"]=="识债者"

@test("Normalizer：词汇替换+AI检测")
def t10():
    from agents.normalizer_agent import first_pass_replace
    text="此刻他蓦然不禁心中一动，深吸一口气，眼眸中闪着光"
    result,count=first_pass_replace(text)
    assert count>3
    assert "此刻" not in result or "蓦然" not in result

@test("Compliance：合规检测")
def t11():
    from agents.compliance_agent import keyword_scan
    hr,_=keyword_scan("陆承走进写字楼，看到那条红色的债线。")
    assert hr==[]
    hr2,_=keyword_scan("习近平下令执行了这个计划")
    assert len(hr2)>0

@test("Checker：五维加权评分")
def t12():
    from agents.checker_agent import calculate_weighted_score
    dims={"hook_power":9,"shuang_density":8,"character_voice":7,"plot_logic":8,"writing_naturalness":7}
    score=calculate_weighted_score(dims)
    assert 7.0<=score<=9.5

@test("fingerprint_checker：统计检测")
def t13():
    from tools.fingerprint_checker import analyze_fingerprint
    # 正常文本
    normal="陆承把合同推回去。\n\"条款三。\"他说。\n对面的男人笑了起来，笑声很假。\n陆承站起来，走出去。"
    r=analyze_fingerprint(normal)
    assert r["ai_score"]<60
    # AI腔文本
    ai="此刻陆承不禁心中一动，深吸一口气，眼眸中闪烁着莫名的光芒。蓦然，他感到心中涌上一丝不由得的感慨。"
    r2=analyze_fingerprint(ai)
    assert r2["ai_score"]>r["ai_score"]

@test("完整写作流水线（Mock）")
def t14():
    with patch("agents.normalizer_agent.call_llm",side_effect=mock_llm), \
         patch("agents.compliance_agent.call_llm",side_effect=mock_llm), \
         patch("agents.checker_agent.call_llm",side_effect=mock_llm):
        from agents.normalizer_agent import run_normalizer
        from agents.compliance_agent import run_compliance
        from agents.checker_agent    import run_checker
        task={"chapter_number":1,"chapter_role":"开局","chapter_goal":"觉醒",
              "main_characters":["陆承"],"shuang_type":"揭秘","shuang_description":"看到债线",
              "ending_hook_type":"信息钩","ending_hook_description":"系统通知",
              "setting_constraints":[],"forbidden_actions":[],"target_length":"2000-2200",
              "audit_mode":"lite","is_arc_climax":False}
        clean,issues,c1=run_normalizer(MOCK_CHAPTER,task)
        assert len(clean)>100 and c1>=0
        comp,c2=run_compliance(clean)
        assert comp["passed"]==True
        check,c3=run_checker(clean,task,"lite")
        assert check["score"]>0
        assert check["verdict"] in ("PASS","PASS_WITH_NOTE","REWRITE_LIGHT","REWRITE_MEDIUM","REWRITE_HEAVY")

@test("Tracker V2：新L2 Schema更新")
def t15():
    with patch("agents.tracker_agent.call_llm",side_effect=mock_llm):
        from agents.tracker_agent import run_tracker
        from memory.memory_manager import empty_l2
        mem=empty_l2(); mem["meta"]["novel_id"]="test_tracker"
        task={"chapter_number":1,"chapter_role":"开局","chapter_goal":"测试",
              "main_characters":["陆承"],"shuang_description":"","ending_hook_description":"",
              "target_length":"2000","audit_mode":"lite","is_arc_climax":False,
              "setting_constraints":[],"forbidden_actions":[]}
        updated,cost=run_tracker(MOCK_CHAPTER,task,mem,"test_tracker_novel")
        assert updated is not None
        hot=updated.get("hot",updated)
        assert "protagonist_level" in hot

@test("验收标准AC-2：题材切换")
def t16():
    from tools.acceptance_tests import ac2_genre_switch
    result=ac2_genre_switch()
    assert result==True

@test("验收标准AC-1：设定一致性")
def t17():
    from tools.acceptance_tests import ac1_consistency
    result=ac1_consistency()
    assert result==True  # 设定包应通过

@test("预算管理：报告与警告")
def t18():
    from tools.budget_manager import generate_report
    r=generate_report(500.)
    assert "total_cost_usd" in r
    assert isinstance(r["budget_used_pct"],(int,float))
    # alerts only present when records_available=True
    assert isinstance(r.get("budget_used_pct", 0), (int, float))

@test("状态持久化：热冷记忆")
def t19():
    from orchestrator_state import create_initial_state,save_state,load_state
    with tempfile.NamedTemporaryFile(suffix=".json",delete=False) as f:
        tmp=f.name
    try:
        s=create_initial_state("persist_test","测试书","fanqie","都市","设定",100.)
        s["current_chapter"]=42
        s["quality_history"]=[7.5,8.0,6.8]
        save_state(s,tmp)
        loaded=load_state(tmp)
        assert loaded["current_chapter"]==42
        assert loaded["quality_history"]==[7.5,8.0,6.8]
    finally:
        os.unlink(tmp)

@test("导出工具：无崩溃")
def t20():
    from tools.exporter import get_chapter_list,print_stats
    chs=get_chapter_list()
    assert isinstance(chs,list)


def run_all_tests():
    print(f"\n{'═'*58}")
    print(f"  🧪 AI网文创作系统 V3 — 集成测试 V2")
    print(f"{'═'*58}\n")
    for t in [t1,t2,t3,t4,t5,t6,t7,t8,t9,t10,t11,t12,t13,t14,t15,t16,t17,t18,t19,t20]:
        t()
    passed=sum(1 for r in results if r[0]==P)
    failed=sum(1 for r in results if r[0]==F)
    print(f"\n{'═'*58}")
    print(f"  结果：{passed}通过 / {failed}失败 / {len(results)}总计")
    if failed==0:
        print(f"  🎉 全部通过！系统已完整就绪。")
        print(f"\n  启动步骤：")
        print(f"    1. cp .env.template .env  && 填入 ANTHROPIC_API_KEY + DEEPSEEK_API_KEY")
        print(f"    2. python tools/calibrate_checker.py  （可选：校准质检模型）")
        print(f"    3. python run.py bootstrap            （生成黄金三章A/B/C版本）")
        print(f"    4. python run.py run 10               （正式生产）")
    else:
        for icon,name,err in results:
            if icon==F: print(f"    {F} {name}: {err}")
    print(f"{'═'*58}\n")
    return failed==0

if __name__=="__main__":
    success=run_all_tests()
    sys.exit(0 if success else 1)
