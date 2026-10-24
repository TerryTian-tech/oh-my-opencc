# Oh My OpenCC 简繁通转换大师 

[![GitHub downloads](https://img.shields.io/github/downloads/TerryTian-tech/OpenCC-DocxConverter/total?style=flat-square)](https://github.com/TerryTian-tech/OpenCC-DocxConverter/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](https://github.com/TerryTian-tech/OpenCC-DocxConverter/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/TerryTian-tech/OpenCC-DocxConverter?style=flat-square&color=bc8cff&labelColor=161b22)](https://github.com/TerryTian-tech/OpenCC-DocxConverter/stargazers)

*交流繁简转换相关问题可进 QQ 群：1055649831*

![](demo-1.png)
![](demo-2.png)

**简繁通转换大师（Oh My OpenCC）** 是一款基于 [OpenCC](https://github.com/BYVoid/OpenCC) 的中文繁简转换软件。

OpenCC 提供了强大的繁简转换核心，而**简繁通转换大师**在 OpenCC 转换能力的基础上，提供了开箱即用的UI界面、多格式文件处理和增强的结巴分词模式，让繁简转换更适合日常文档、电子书、字幕、批量处理以及古籍/文言文等场景。

与原版 OpenCC 相比，**简繁通转换大师**主要提供：

1. **图形界面（GUI）**：原版 OpenCC 是命令行工具，**简繁通转换大师**为 OpenCC 的绝大部分功能提供UI界面，便易操作。
2. **多格式文件转换**：原版 OpenCC 主要面向 UTF-8 编码的 TXT 文件，**简繁通转换大师**支持 Word 文档（DOCX）、文本文件（TXT、MD）、电子书（EPUB）和字幕文件（SRT、ASS/SSA、LRC）的转换。此外，还有限支持 PDF 文件的转换。
3. **结巴分词模式增强**：在原版 OpenCC 提供结巴分词功能的基础上，针对不同转换模式，定制结巴分词词库，区分现代汉语、古代汉语分词词典，提高复杂文本的转换准确度。
4. **《通用规范汉字表》繁体转换词典**：支持将简体和不同标准的繁体转换为《通用规范汉字表》附件1标准的繁体。

|  对比项  | 原版 OpenCC | 简繁通转换大师 |
|----------|-------------|--------------|
| 使用方式 | 命令行 | 图形界面 GUI，支持批量转换、进度显示、转换日志 |
| 输入格式 | 主要支持 UTF-8 编码的 TXT | DOCX、TXT、MD、EPUB、SRT、ASS/SSA、LRC，有限支持 PDF 转换 |
| 编码处理 | 主要依赖 UTF-8 | 自动检测编码，并提供用户强制编码选项 |
| 分词增强 | 需自行处理或配置 | 结巴分词预处理，支持现代汉语、古代汉语分词词典，并针对不同模式定制 |
| 格式保留 | 纯文本转换 | 保留 DOCX 文档原有格式、字幕时间码/样式保留、PDF 版式按原样重建 |
| 转换模式 | 19 种转换模式 | 内置 22 种模式，增加《通用规范汉字表》繁体转换词典 |

## 主要特性

### 1. 图形界面（GUI）

原版 OpenCC 以命令行为主，适合开发者和熟悉命令行的用户。**简繁通转换大师**提供完整的图形界面，降低使用门槛：

- 可视化选择文件、文件夹和转换模式
- 支持文件夹级批量转换
- 实时显示处理进度和转换日志
- 内置“文字转换”页面，左侧输入、右侧输出，即时转换整段文字
- 转换设置、自定义转换表、分词模式等均可通过界面配置

### 2. 多格式文件支持

原版 OpenCC 主要处理 UTF-8 编码的 TXT 文本。**简繁通转换大师**扩展了文件处理能力，支持多种常见格式，并针对不同格式做专门处理：

| 文件格式 | 说明 | 特殊处理 |
|---------|------|---------|
| **DOCX** | Microsoft Word 文档 | 保留原有格式、排版，可选择是否转换页眉页脚、脚注尾注 |
| **TXT** | 纯文本文件 | 自动检测编码并转换为 UTF-8 |
| **MD** | Markdown 文本 | 转换文本内容 |
| **EPUB** | 电子书文件 | 转换文本内容 |
| **PDF** | PDF | 有限支持：按原版式重建，保留文字位置、字号、颜色、图片和矢量图形，不支持扫描件 |
| **SRT** | SubRip 字幕文件 | 保留时间码，支持 ASS/SSA 样式标签 |
| **ASS/SSA** | Advanced SubStation Alpha 字幕 | 保留样式定义，仅转换对话文本 |
| **LRC** | 歌词文件 | 保留时间标签和增强型标签 |

**智能编码检测：**

- 采用 Chardet 库进行文件编码自动识别
- 特别优化 GB2312、GBK、GB18030 等中文编码的处理
- 支持多种中文编码的智能识别与兼容读取

**文档格式保留：**

- DOCX 文档转换后完整保留原有格式
- 支持字体、颜色、大小、粗体、斜体、下划线等格式属性
- 支持页眉、页脚、表格等复杂文档元素
- 可选转换脚注和尾注内容

**字幕文件智能处理：**

- SRT 字幕：保留序号和时间码，仅转换字幕文本
- ASS/SSA 字幕：保留样式标签 `{...}`，只转换显示文本
- LRC 歌词：保留时间标签 `[mm:ss.xx]` 和增强型标签 `<xx>`

### 3. 结巴分词模式增强

Oh My OpenCC 为不同模式定制了结巴分词词库，支持在转换前使用结巴分词进行预处理，转换后清除分词标记，从而提高词汇级转换的准确度。

- 支持现代汉语分词词典
- 支持古代汉语分词词典，适合古籍、文言文等文本
- 可针对不同转换组件和场景选择是否启用结巴分词

对于“干”“发”“后”等一对多歧义字词，结合分词和自定义转换表可以获得更符合语境的结果。

### 4. 丰富的转换标准

程序内置 22 种转换模式，覆盖主流的繁简转换需求：

**基础转换模式：**

- 简体 → 繁体（OpenCC 标准）
- 繁体 → 简体（OpenCC 标准）

**地区标准转换：**

- 简体 ↔ 台湾正体
- 简体 ↔ 香港繁体
- 繁体 ↔ 台湾正体
- 繁体 ↔ 香港繁体

**《通用规范汉字表》标准转换：**

- 简体 ↔ 繁体（《通用规范汉字表》标准）
- 繁体 → 繁体（《通用规范汉字表》标准）

**词汇转换模式：**

- 简体 → 繁体（台湾标准）并转换为台湾常用词汇
- 繁体（台湾标准）→ 简体并转换为大陆常用词汇
- 简体 → 繁体（香港标准）并转换为香港常用词汇
- 繁体（香港标准）→ 简体并转换为大陆常用词汇

**日文汉字转换：**

- 繁体（OpenCC 标准，旧字体）↔ 日文新字体

**小篆*字体转换**

- 繁体汉字 ↔ 小篆（Unicode 18.0 篆书区块 U+3D000..U+3FC3F）
- 简体汉字 → 小篆（Unicode 18.0 篆书区块 U+3D000..U+3FC3F）

*小篆显示需要安装特殊的字体文件。请前往 [seal-sans](https://github.com/TerryTian-tech/seal-sans) 仓库下载 SealSans-Regular_v1.otf 字体文件并双击打开，安装至系统中。该字体作者为 [Ghimist](https://github.com/Ghimist) ，基于 AGPL-3.0 开源协议许可。

### 5. 文字转换与一对多歧义标注

基于 OpenCC 原版标注功能实现，在UI内操作简单：

- 内置“文字转换”页面，左侧输入、右侧输出，即时完成整段文字的繁简转换
- 支持全部 22 种转换模式，与设置中的分词模式、自定义转换表联动
- 转换结果中的一对多歧义词（如简体“干”可对应“乾/幹”）以红色波浪线标注
- 点击波浪线可查看该词的全部候选转换值并一键替换

### 6. 批量处理能力

- 支持文件夹级别的批量转换
- 自动识别文件夹内所有支持的文件格式
- 实时显示处理进度和转换日志

### 7. 自定义转换表

基于 OpenCC 原版“内联字典（inline dictionary）”功能实现，操作简单：

- 可指定一个转换类型应用自定义规则，自定义规则先于内置词典生效
- 规则格式简单直观：每行一条 `原词→目标词`，支持从文件导入、导出
- 无需修改任何内置配置或词典文件

## 安装与运行

### 方式一：下载发行版（Windows 10/11）

直接从 [Releases](https://github.com/TerryTian-tech/OpenCC-DocxConverter/releases) 页面下载对应平台的压缩包，解压后即可运行，无需配置 Python 环境。

### 方式二：从源码运行（Windows/Linux）

在 Windows 系统上，使用者需预先部署 Python 运行环境。然后打开终端（PowerShell），执行以下命令安装依赖并运行：

```powershell
git clone https://github.com/TerryTian-tech/OpenCC-DocxConverter.git
cd OpenCC-DocxConverter/opencc-docxconverter
pip install -r requirements.txt
Copy-Item -Path "..\dict\*" -Destination "$(python -c "import opencc, os; print(os.path.join(os.path.dirname(opencc.__file__), 'clib', 'share', 'opencc'))")" -Recurse -Force
$dest = python -c "import opencc, os; print(os.path.join(os.path.dirname(opencc.__file__), 'clib', 'share', 'opencc', 'jieba_dict'))"
$null = New-Item -ItemType Directory -Path $dest -Force; Copy-Item -Path "..\jieba\*" -Destination $dest -Recurse -Force
python main.py
```

在 Linux 发行版下，使用者需预先部署 Python 运行环境，然后打开终端，执行以下命令安装依赖并运行：

```bash
git clone https://github.com/TerryTian-tech/OpenCC-DocxConverter.git
cd OpenCC-DocxConverter/opencc-docxconverter
pip install -r requirements.txt
cp -rf ../dict/* "$(python3 -c "import opencc, os; print(os.path.join(os.path.dirname(opencc.__file__), 'clib', 'share', 'opencc'))")"
DEST="$(python3 -c "import opencc, os; print(os.path.join(os.path.dirname(opencc.__file__), 'clib', 'share', 'opencc', 'jieba_dict'))")"
mkdir -p "$DEST" && cp -rf ../jieba/* "$DEST"
python3 main.py
```

结巴分词支持词典位于 `jieba` 路径下，其中现代汉语分词词典来自[结巴分词仓库](https://github.com/fxsjy/jieba)，古汉语分词默认词典使用了 [Dingyuan Wang](https://github.com/gumblex) 制作的 [jiebazhc](https://github.com/The-Orizon/nlputils) 。如需结巴分词功能，请前往OpenCC官方仓库提取（[Windows](https://github.com/BYVoid/OpenCC/releases/download/ver.1.4.2/OpenCC-1.4.2-windows-x64-portable.zip)、[Linux](https://github.com/BYVoid/OpenCC/releases/download/ver.1.4.0/opencc-jieba_1.4.0_amd64.deb)） `bin\plugins` 下的文件复制到你本地的OpenCC目录下（可运行 `pip show opencc` 命令查看OpenCC所在位置）。

注意：Python 包内的 opencc 命令行工具为静态构建，无法加载依赖 opencc.dll 的分词插件；若要在“文字转换”中使用结巴分词，需将 OpenCC 官方仓库最新构建的动态库版原生构建的 `opencc.exe`、`opencc.dll` 与 `plugins/opencc-jieba.dll` 一并复制到opencc包的 `clib/bin/` 目录下。此问题待 OpenCC 正式发布 1.4.3 后即可改善。

## 项目结构

```
OpenCC-DocxConverter/
├── opencc-docxconverter/     # 主程序目录
│   ├── main.py               # 主程序入口，GUI界面与程序逻辑
│   ├── doc_converter.py      # Word文档(DOCX)转换模块
│   ├── text_converter.py     # 文本文件(TXT/SRT/ASS/SSA/LRC)转换模块
│   ├── ambiguity_text.py     # 文字转换页模块，包含文字转换与一对多歧义标注
│   ├── epub_converter.py     # 电子书文件(EPUB)转换模块
│   ├── pdf_converter.py      # PDF文件转换模块
│   ├── custom_dict.py        # 自定义词典模块
│   ├── updater.py            # 更新检查模块
│   ├── constants.py          # 版本常量
│   ├── requirements.txt      # Python 依赖列表
│   └── logo.ico              # 程序图标
├── dict/                     # 转换词典目录
├── jieba/                    # 结巴分词词典目录
└── README.md                 # 项目说明文档
```

## 第三方库

| 组件 | 版本 | 说明 |
|-----|------|-----|
| [OpenCC](https://github.com/BYVoid/OpenCC) | 1.4.2 | 开源中文繁简转换库 |
| [Python-docx](https://github.com/python-openxml/python-docx) | 1.2.0 | Word 文档处理库 |
| [PySide6](https://www.qt.io/qt-for-python) | 6.11.2 | Qt for Python GUI 框架 |
| [Chardet](https://github.com/chardet/chardet) | 7.7.0 | 字符编码检测库 |
| [Certifi](https://pypi.org/project/certifi/) | 2026.10.23 | Mozilla 根证书库 |
| [Beautifulsoup4](https://pypi.org/project/beautifulsoup4/) | 4.15.0 | HTML 和 XML 文档解析库 |
| [lxml](https://github.com/lxml/lxml) | 6.1.3 | 大型文档和 XML 处理库 |
| [pdf-oxide](https://pypi.org/project/pdf-oxide/) | 0.3.78 | PDF 文件解析与重建库 |
| [Pillow](https://python-pillow.github.io/) | 13.0.0 | 图像处理库，用于 PDF 扫描页栅格化保留 |
| [OpenCC-Traditional Chinese to Traditional Chinese (The Chinese Government Standard)](https://github.com/TerryTian-tech/OpenCC-Traditional-Chinese-characters-according-to-Chinese-government-standards) | 1.4.5 | 《通用规范汉字表》标准转换词典 |

## 隐私与安全

- **本地处理**：所有文件转换均在本地完成，不会上传至任何服务器，保障您的数据安全
- **无网络依赖**：核心功能完全离线可用（更新检查除外）
- **开源透明**：完整源代码公开，可供安全审计

## 开源协议

Apache-2.0 LICENSE

`jieba` 目录下的 `dict.txt` 来自 [结巴分词仓库](https://github.com/fxsjy/jieba)，`dict_ancient_chinese.txt` 和 `dict_ancient_chinese_traditional.txt` 来自 [Dingyuan Wang](https://github.com/gumblex) 制作的 [jiebazhc](https://github.com/The-Orizon/nlputils)。以上文件遵循 MIT License 开源，特此说明。