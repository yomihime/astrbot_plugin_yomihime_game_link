# 子任务派发模板

当前内核执行使用 [CORE-HARDENING-01](../../.coordination/tasks/core/CORE-HARDENING-01.md) 内的角色子任务。以下模板用于派发消息或后续精确子卡，不再生成另一份总计划。规则见[协作协议](orchestration.md)。

```text
任务 ID / 所属总卡 / 波次：
状态：ready（必须已核对前置，不能预填实现完成）
实现 agent / reviewer：
起始 HEAD + dirty/untracked 内容指纹：
公共契约与实际输入签名：
目标：一个可观察交付
允许修改：精确源码、测试、handoff 路径；注明新增文件
禁止范围：其他 owner、产品选择、发布/部署等
依赖：编码依赖及集成依赖分开；已审实际接口路径
步骤：从现有实现迁移，避免双轨，明确失败/回滚
验收 ID：正常 / 权限 / 并发 / 失败 / 恢复
验证命令：实际可执行，记录退出码和 skip
交接：diff/hash、合同 revision、逐项结果、替身与目标环境边界
完成后：停止写入，交独立 reviewer；发现问题由原 owner 修复
```

文件冲突或缺少接口时向 root 提交现有签名、最小改动与受影响消费者；root 在总卡范围内协调所有权。不得等待历史聊天 owner、擅自绕过服务、删除回归或把集成缺口留成无 owner 的 TODO。
