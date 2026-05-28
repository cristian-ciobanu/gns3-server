<!--
SPDX-License-Identifier: CC-BY-SA-4.0
See LICENSE file for licensing information.
-->

# GNS3-Copilot 实时数据包 AI 分析架构

## 核心流程

```mermaid
flowchart TB
    subgraph "① 分析触发与知识查询"
        A["用户提问\n如'分析OSPF邻居状态'"] --> B["LLM调用\nPacketAnalysisSkillsTool"]
        B --> C{"协议知识仓库\ngns3/gns3-skills"}
        C --> D["返回协议定义\nfields/base_filter/check_rules"]
        B --> E["LLM调用\nsearch_fields模式"]
        E --> F["tshark -G fields\n字段名搜索"]
        F --> G["返回有效字段名"]
    end

    subgraph "② 实时捕获与分析"
        D --> H["LLM构造tshark_args"]
        G --> H
        H --> I["PacketAnalysisTool\ncapture分析模式"]
        I --> J["GET /capture/file\n下载实时PCAP"]
        J --> K["预验证-e字段名"]
        K --> L["tshark -r pcap\n执行分析"]
        L --> M["返回分析结果"]
    end
```

## 工具总览

| 工具 | 源文件 | 作用 | 可用模式 |
|---|---|---|---|
| `PacketAnalysisTool` | `packet_analysis_tool.py` | 下载实时 PCAP + tshark 分析 | teaching / lab_automation |
| `PacketAnalysisSkillsTool` | `registry.py`（skills 模块） | 查询协议级分析知识（字段、过滤规则） | teaching / lab_automation |


## Agent 工作流（LangGraph）

```mermaid
sequenceDiagram
    participant U as User
    participant LLM as LLM Node
    participant Skills as PacketAnalysisSkillsTool
    participant Pcap as PacketAnalysisTool

    U->>LLM: OSPF邻居无法建立，分析一下
    LLM->>Skills: get protocol=ospf
    Skills-->>LLM: ospf字段、filter定义
    LLM->>Pcap: search_fields query=ospf.hello
    Pcap-->>LLM: 有效-e字段名
    LLM->>Pcap: 下载PCAP + tshark_args
    Pcap-->>LLM: tshark输出结果
    LLM->>LLM: 分析发现Dead间隔不匹配
    LLM-->>U: OSPF Dead间隔不一致
```

## 服务端 Capture API

| 端点 | 功能 |
|---|---|
| `POST /v3/projects/{pid}/links/{lid}/capture/start` | 启动链路上的数据包捕获 |
| `POST /v3/projects/{pid}/links/{lid}/capture/stop` | 停止捕获 |
| `GET /v3/projects/{pid}/links/{lid}/capture/file` | 下载 PCAP 文件（捕获进行中也可下载） |
| `GET /v3/projects/{pid}/links/{lid}/capture/stream` | 流式传输 PCAP 数据 |
| `WS /v3/projects/{pid}/links/{lid}/capture/web-wireshark` | Web Wireshark WebSocket 代理 |

## 关键设计要点

1. **LLM 主导分析** — LLM 自行构造 tshark 参数，框架不做协议硬编码，只做安全验证
2. **实时 PCAP** — 捕获运行时即可下载分析，无需停止抓包
3. **双重知识源** — 外部仓库提供协议预定义知识，本地 tshark field registry 提供精确字段名
4. **安全前置** — tshark 字段名预验证，避免无效字段导致执行失败
