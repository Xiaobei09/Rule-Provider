# Rule-Provider

[![CI](https://github.com/Xiaobei09/Rule-Provider/actions/workflows/ci.yml/badge.svg)](https://github.com/Xiaobei09/Rule-Provider/actions/workflows/ci.yml)
[![Daily Update](https://github.com/Xiaobei09/Rule-Provider/actions/workflows/daily-update.yml/badge.svg)](https://github.com/Xiaobei09/Rule-Provider/actions/workflows/daily-update.yml)

**全球国家/地区 IP 规则集（Rule Provider）+ 全球规则集 + 各国域名（site）规则集，每日自动生成并提交。**

项目组织风格参考 [blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script)：
多平台格式分目录输出，规则集纯净、可直接引用。

---

## 特性

- **国家数量齐全**：五大 RIR 权威数据合并，覆盖 **239** 个国家/地区
  （ISO 3166-1 全集 249 个中，仅无人岛/无分配领地缺席，另有 `EU` 欧盟特殊码）；
- **准确性高**：数据来自 IANA 授权的区域互联网注册机构（AFRINIC / APNIC / ARIN /
  LACNIC / RIPE NCC）第一手分配记录，自动剔除 IANA 保留地址段；
  另以 IPtoASN（ASN 注册国，Public Domain）作并集补充，弥合「分配国」与
  「运营国」错位的跨国地址段；
- **全球规则集**：额外提供 `Global` 规则集（全部国家地址段并集，IPv4 + IPv6）；
- **各国域名规则集（site）**：`ruleset/site/` 提供每国域名规则——各国 ccTLD
  （`.cn`、`.de`、`.jp` 等，v2fly 全量清单 + IANA 补全）+ 精选分类
  （中国含 v2fly geolocation-cn 富域名列表，俄罗斯含 `.ru/.su/.moscow` 等）
  + 各国**热门网站 Top 5000**（Chrome CrUX 按国家排名，经 Public Suffix List
  规范化），并附 Global 域名并集；
- **多格式输出**：Surge / Clash / QuantumultX / Loon；
- **每日自动更新**：GitHub Actions 每天 04:30 UTC 抓取最新数据、重新生成、
  有变化即自动提交；
- **可复现**：确定性输出 + 幂等性检查 + 完整单元测试。

---

## 目录结构

```
.
├── config/config.yaml          # 生成配置（数据源/格式/约束开关）
├── scripts/                    # 生成器（零第三方依赖）
│   ├── generate.py             # CLI 入口
│   ├── sources.py              # 数据源下载与解析
│   ├── iptoasn.py              # geoip：IPtoASN 补充（ASN 归属）/ 范围拆分
│   ├── cidr.py                 # CIDR 合并 / 保留段过滤
│   ├── render.py               # 各平台格式渲染
│   ├── countries.py            # ISO 3166 中英文元数据
│   ├── geosite.py              # site：v2fly 分类 / ccTLD 映射
│   ├── topsites.py             # site：各国热门网站（CrUX）/ PSL 规范化
│   └── config.py               # 轻量 YAML 解析
├── ruleset/                    # ← 生成产物（每日自动更新）
│   ├── metadata.json           # geoip 全量元数据
│   ├── geoip/{Surge,Clash,QuantumultX,Loon}/<CC>.*
│   ├── global/{Surge,Clash,QuantumultX,Loon}/Global.*
│   └── site/{Surge,Clash,QuantumultX,Loon}/<CC>.*   # 各国域名规则集
├── .github/workflows/          # CI + 每日更新工作流
├── tests/                      # 单元测试
├── run_tests.py                # 零依赖测试运行器
├── README.md
└── DEVELOPMENT.md              # 生成细节约束与开发规范
```

---

## 使用方法

### Surge

```ini
[Rule]
RULE-SET,https://raw.githubusercontent.com/Rule-Provider/Rule-Provider/master/ruleset/geoip/Surge/CN.list,DIRECT
RULE-SET,https://raw.githubusercontent.com/Rule-Provider/Rule-Provider/master/ruleset/global/Surge/Global.list,PROXY
```

### Clash（rule-providers）

```yaml
rule-providers:
  cn:
    type: http
    behavior: ipcidr
    url: "https://raw.githubusercontent.com/Rule-Provider/Rule-Provider/master/ruleset/geoip/Clash/CN.yaml"
    path: ./ruleset/geoip/CN.yaml
    interval: 86400
  cn-site:
    type: http
    behavior: classical
    url: "https://raw.githubusercontent.com/Rule-Provider/Rule-Provider/master/ruleset/site/Clash/CN.yaml"
    path: ./ruleset/site/CN.yaml
    interval: 86400

rules:
  - RULE-SET,cn,DIRECT
  - RULE-SET,cn-site,DIRECT
  - MATCH,PROXY
```

> site（域名）规则集在 Clash 中必须使用 `behavior: classical`。

### Quantumult X

```text
[filter_remote]
https://raw.githubusercontent.com/Rule-Provider/Rule-Provider/master/ruleset/geoip/QuantumultX/CN.txt, tag=中国IP, enabled=true
https://raw.githubusercontent.com/Rule-Provider/Rule-Provider/master/ruleset/site/QuantumultX/CN.txt, tag=中国域名, enabled=true
```

### Loon

```ini
[Remote Rule Set]
https://raw.githubusercontent.com/Rule-Provider/Rule-Provider/master/ruleset/geoip/Loon/CN.list, tag=中国IP, enabled=true
https://raw.githubusercontent.com/Rule-Provider/Rule-Provider/master/ruleset/site/Loon/CN.list, tag=中国域名, enabled=true
```

> 国家代码使用 ISO 3166-1 alpha-2（如 `CN`、`US`、`JP`、`HK`、`TW`），
> 特殊代码 `EU` 代表欧盟。完整国家列表见 `ruleset/metadata.json`
> （IP）与 `ruleset/site/metadata.json`（域名）。

---

## 数据快照

> 由每日工作流自动更新，非手动维护。

- IP 数据来源：AFRINIC / APNIC / ARIN / LACNIC / RIPE NCC
  （可选补充 MaxMind GeoLite2；并集补充 IPtoASN）
- site（域名）数据来源：v2fly/domain-list-community（各国 ccTLD + 精选分类，
  含代表性应用/公司分类归属母国，如 US=google/netflix/openai 等）
  + Chrome CrUX 各国热门网站 Top 5000（Public Suffix List 规范化）
- 覆盖国家/地区数：IP 241 / 域名 243（不含无分配地址或无 ccTLD 的领地；特殊码 `EU` 一并提供）
- 全球规则集规模：约 17.5 万条 CIDR（IPv4 约 11.1 万 / IPv6 约 6.9 万）；
  Global site 约 22.7 万条域名规则
- 最新数据日期：见 [`ruleset/metadata.json`](ruleset/metadata.json)
  与 [`ruleset/site/metadata.json`](ruleset/site/metadata.json)

---

## 本地使用

固定开发命令见 [`Makefile`](Makefile)（与 CI 各步骤一一对应）：

```bash
make test        # 运行全部单元测试（零依赖）
make fetch       # 下载数据源到 cache/（需联网）
make generate    # 生成规则集到 ruleset/
make validate    # 校验已生成规则集
make check       # 生成 + 校验 + 幂等性检查
make all         # 完整流程（fetch + check）
make clean       # 删除生成产物 ruleset/
```

等价底层命令：

```bash
# 下载并解析数据源（写入 cache/，不入库）
python3 scripts/generate.py fetch

# 生成全部规则集
python3 scripts/generate.py generate

# 校验已生成规则集
python3 scripts/generate.py validate

# 运行单元测试（零依赖）
python3 run_tests.py

# 常用选项
python3 scripts/generate.py generate --countries CN,US,JP --formats Clash --no-global --print-stats
python3 scripts/generate.py generate --no-site   # 只生成 IP 规则集，跳过域名规则集
```

详细生成细节约束、数据源解析规则、开发与提交规范见 [DEVELOPMENT.md](DEVELOPMENT.md)。

---

## 准确性说明

- IP 数据来自五大 RIR 的 delegated 扩展文件，是 IP 分配的**权威记录**；
  各文件仅含本区域数据，本项目合并全部五个以获得全球覆盖。
- 生成时剔除 IANA 特殊用途保留段（私网/回环/多播/TEST-NET 等）；
- 相邻地址段自动合并为更大 CIDR，不改变地址归属；
- 并集补充 IPtoASN（ASN 注册国）：跨国段同时进入「分配国」与「运营国」，
  弥合 RIR 只记录分配国的盲区；可选启用 MaxMind GeoLite2 作另一补充来源。
- 域名数据来自 v2fly/domain-list-community：ccTLD 按国家注释映射 + IANA 官方
  根区列表校验补全，国家归属以数据源标注为准；
- 各国热门网站来自 Chrome CrUX 公开排名（每月更新）：仅取每国 Top 5000，
  域名经 Public Suffix List 规范化为可注册域名，被该国 ccTLD 后缀覆盖的自动剔除。

---

## 特别声明

1. 本项目数据来源于公开的权威注册机构，仅用于学习与网络分流研究；
2. 本项目无法保证内容的绝对准确性、完整性，规则可能随网络环境变化失效；
3. 使用本项目造成的一切后果，与本项目贡献者无关；
4. 禁止将本项目内容用于违反所在国家/地区法律法规的其他用途；
5. 本项目源代码基于 MIT 许可开源。

---

## 许可证

[MIT](LICENSE)

数据来源致谢：[AFRINIC](https://www.afrinic.net/) ·
[APNIC](https://www.apnic.net/) · [ARIN](https://www.arin.net/) ·
[LACNIC](https://www.lacnic.net/) · [RIPE NCC](https://www.ripe.net/) ·
[MaxMind](https://www.maxmind.com/) ·
[IPtoASN](https://iptoasn.com/) ·
[v2fly](https://github.com/v2fly/domain-list-community) ·
[Google Chrome UX Report](https://developer.chrome.com/docs/crux/) ·
[publicsuffix.org](https://publicsuffix.org/)
