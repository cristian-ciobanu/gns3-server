<!--
SPDX-License-Identifier: CC-BY-SA-4.0
See LICENSE file for licensing information.
-->

# GNS3-Copilot 故障注入概览

## 核心流程

```mermaid
flowchart TB
    subgraph "① 接口触发与模式切换"
        A["POST /chat/inject\n用户请求注入故障"] --> B["验证项目已打开"]
        B --> C["设置copilot_mode =\ntroubleshooting_injection"]
        C --> D["启动Agent\n携带故障注入工具集"]
    end

    subgraph "② 拓扑分析与故障选型"
        D --> E["GNS3TopologyTool\n获取拓扑信息"]
        E --> F["ExecuteMultipleDeviceCommands\n获取设备配置"]
        F --> G["InjectionSkillsTool\n查询可用故障类型"]
        G --> H{"注入技能仓库\ngns3/gns3-skills"}
        H --> I["返回匹配的故障定义\n含配置注入命令"]
    end

    subgraph "③ 故障注入"
        I --> J["选择注入方式"]
        J --> K["ExecuteMultipleDeviceConfigCommands\n注入配置变更"]
        J --> L["GNS3PacketFilterTool\n注入链路层故障"]
    end

    subgraph "④ 结果确认"
        K --> M["验证故障生效"]
        L --> M
        M --> N["记录故障详情\n含恢复命令"]
    end
```

## 工具总览

| 工具 | 源文件 | 作用 | 可用模式 |
|---|---|---|---|
| `InjectionSkillsTool` | `registry.py`（skills 模块） | 查询协议级故障定义（配置变更命令） | troubleshooting_injection |
| `GNS3PacketFilterTool` | `gns3_packet_filter.py` | 链路层故障注入（延迟、丢包、损坏、BPF） | troubleshooting_injection |
| `ExecuteMultipleDeviceConfigCommands` | `config_tools_nornir.py` | 批量执行设备配置变更 | troubleshooting_injection |
| `ExecuteMultipleDeviceCommands` | `display_tools_nornir.py` | 读取设备配置（只读） | troubleshooting_injection |
| `GNS3TopologyTool` | `gns3_client` | 获取项目拓扑信息 | troubleshooting_injection |

## 故障注入 API

| 端点 | 功能 |
|---|---|
| `POST /v3/projects/{pid}/chat/inject` | 触发故障注入，设置 `troubleshooting_injection` 模式后启动 Agent |

**前置条件**：项目必须为 `opened` 状态，否则返回 403。

## GNS3PacketFilterTool 链路滤波器

| 滤波器类型 | 功能 | 参数 |
|---|---|---|
| `delay` | 延迟 + 抖动 | `[latency(0-32767), jitter(0-32767)]` |
| `packet_loss` | 丢包率 | `[chance(0-100)]` |
| `corrupt` | 包损坏率 | `[chance(0-100)]` |
| `frequency_drop` | 每 N 包丢弃一个 | `[frequency(-1~32767)]` |
| `bpf` | Berkeley Packet Filter | 表达式文本 |

## Agent 工作流（LangGraph）

```mermaid
sequenceDiagram
    participant U as User
    participant API as POST /chat/inject
    participant LLM as LLM Node
    participant Topo as GNS3TopologyTool
    participant DC as ExecuteMultipleDeviceCommands
    participant CC as ExecuteMultipleDeviceConfigCommands
    participant Skill as InjectionSkillsTool
    participant Filter as GNS3PacketFilterTool

    U->>API: 注入一个OSPF故障
    API->>LLM: 设置mode=troubleshooting_injection
    LLM->>Topo: 获取拓扑
    Topo-->>LLM: 拓扑信息
    LLM->>DC: 查看设备配置
    DC-->>LLM: Running配置
    LLM->>Skill: list context=["ospf"]
    Skill-->>LLM: 匹配的故障类型
    LLM->>Skill: get device_type=injection_ospf
    Skill-->>LLM: 故障定义+注入命令
    LLM->>CC: 执行配置注入
    CC-->>LLM: 注入结果
    LLM->>Filter: set filters={delay:[200,50]}
    Filter-->>LLM: 链路延迟注入成功
    LLM-->>U: 故障已注入，含恢复命令
```

## 关键设计要点

1. **专用 API 入口** — `POST /chat/inject` 端点专门用于故障注入，自动切换为 `troubleshooting_injection` 模式
2. **LLM 主导故障选型** — LLM 分析拓扑后通过 `InjectionSkillsTool` 查询匹配协议栈的故障，不硬编码故障场景
3. **双层注入** — 设备级配置变更 + 链路级网络损伤，覆盖完整排错场景
4. **故障可逆** — 每条注入均附带恢复命令，链路滤波器可通过 `action: clear` 一键清除
5. **安全前置** — BPF 语法通过 tshark 预验证，配置命令受 `command_filter` 限制
6. **上下文过滤** — `InjectionSkillsTool` 强制要求传入 `context` 参数，只返回与拓扑协议匹配的故障
