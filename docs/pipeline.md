# v0.2 当前流程与开发边界

v0.2 从统一 `task_definition` 生成 Query、答案形式和 evaluator；保留与原 bench 接近的任务和答案表达，继续只做单站任务。公开任务冻结后，强模型必须在独立基线上仅凭公开输入执行完整 GUI 轨迹。评分分别记录答案、网络证据和最终持久状态，保留所有失败及隔离状态。

| 阶段 | 当前 owner / 输入输出 |
| --- | --- |
| 1. 家族选择 | 抽象 WebHarbor families 与 Qwen 全量结果分析；不读取官方具体任务 |
| 2. 基线 | 执行协调器提供 pinned site image、干净状态和 baseline receipt |
| 3. Grounding | GUI 观察、HAR、trace、截图、字段级 evidence；`collect_reference.py` 提供采集入口，站点操作仍需具体计划 |
| 4. 统一编译 | `author.py` + `contract.py`：draft -> private/public JSONL、任务定义和 evaluator |
| 5. Author 验证 | `replay.py`：正例、错误回答、do-nothing、关键参数负控、HAR/Playwright 一致性 |
| 6. Evaluator | `prepare_evaluator.py` 构建隔离的 AR null 修复，不修改安装包 |
| 7. 污染检查 | `validate.py`：生成完成后才读取 SHA 固定的官方 catalog |
| 8. 独立参考执行 | `run_reference.py` + `generated_env.py`：公开任务、真实模型、干净执行基线、完整轨迹 |
| 9. 分层评分 | `generated_env.py`：AR、NE 和 terminal persisted state；历史失败不覆盖 |
| 10. 汇总 | `quality_check.py`：机器 gate 与人工 review packet；人工决策单独记录 |
| 11. 数据准入 | 由 CUA-Lite 原 canonical stage/export owner 执行；当前仍待完成 |

本仓库提供当前实际使用的作者/验证代码与 runtime 补丁。沙盒资源申请、每站点的现场 grounding、认证和状态恢复仍由执行协调器完成，本次封存没有将批次临时操作脚本包装成新的全自动调度系统。

## 参考执行

模型配置、站点 URL、可选认证 storage state 都由执行者私下提供。输出目录必须全新，baseline receipt 必须绑定单个任务及干净 snapshot：

```bash
uv run --no-sync python devs/data/webarena_verified_generated/run_reference.py \
  --private /private/batch/private.jsonl \
  --generated-task-id wavgen-v02-pilot30-0001 \
  --site-urls /private/site-urls.json \
  --baseline-receipt /private/fresh-baseline.json \
  --model-config /private/model.json \
  --model-id ACTUAL_MODEL_ID \
  --output-dir /new-output/reference_runs/wavgen-v02-pilot30-0001
```

需要预先设置 README 中的 runtime 与隔离 evaluator `PYTHONPATH`。调用 `--dry-run` 可验证输入与公开 prompt；它不会执行模型或消耗基线。此入口只启动自己的浏览器 context，不负责创建或清理站点服务器。

## 本轮基线与后续优先事项

当前数据为 30 条候选，25 条参考通过、5 条隔离，人工与训练准入 gate 均未完成。详细逐 case 结果见 release 目录的 score matrix 和 failure audit，完整证据见共享批次。

后续优先修复的三组 owner 问题：

1. 任务语义和答案精度：显式约定 Wiki 日期精度，避免公开 Query 与私有答案的月/年要求不一致。
2. NetworkEventEvaluator：支持已证实等价的合法路由和默认参数；同时保留关键参数负控，避免放宽后误得分。
3. 轨迹与终态评分：区分异步请求结束顺序和用户动作完成语义，消除 HAR/Playwright last-event 顺序导致的误判。

每次行为变化都从原失败 case 的冻结证据建立回归验证，记录新结果而非改写本轮结果，再对公开任务做独立参考执行。
