import re
import time
import os
import json
import tkinter as tk
from tkinter import filedialog, ttk, messagebox, scrolledtext, Listbox, END, simpledialog
import requests
import threading
from typing import List, Dict, Optional, Tuple

# 尝试检测和读取不同编码的文件
def read_file_with_encoding(file_path: str) -> str:
    """尝试使用多种编码读取文件，解决编码错误问题"""
    # 常见的字幕文件编码
    encodings = ['utf-8', 'latin-1', 'gbk', 'gb2312', 'utf-16', 'iso-8859-1']
    
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                return f.read(), encoding
        except UnicodeDecodeError:
            continue
        except Exception as e:
            continue
    
    # 如果所有编码都尝试失败，使用替换错误模式最后尝试一次
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read(), 'utf-8 (with replacement)'
    except Exception as e:
        raise Exception(f"无法读取文件 {file_path}，尝试了多种编码均失败: {str(e)}")


class TranslationProgress:
    """翻译进度记录类，用于实现断点续译"""
    
    def __init__(self):
        self.progress_file = ".translation_progress.json"
        self.reset()
        
    def reset(self):
        """重置进度记录"""
        self.data = {
            "overall_progress": 0,
            "current_file_index": 0,
            "files": {}  # 格式: {file_path: {"completed": True/False, "subtitle_index": 已完成的字幕索引}}
        }
        
    def load(self):
        """从文件加载进度记录"""
        try:
            if os.path.exists(self.progress_file):
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                return True
        except Exception as e:
            print(f"加载进度记录失败: {e}")
        return False
        
    def save(self):
        """保存进度记录到文件"""
        try:
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存进度记录失败: {e}")
            return False
            
    def clear(self):
        """清除进度记录文件"""
        self.reset()
        if os.path.exists(self.progress_file):
            try:
                os.remove(self.progress_file)
                return True
            except Exception as e:
                print(f"清除进度记录失败: {e}")
        return False
            
    def set_file_progress(self, file_path: str, completed: bool, subtitle_index: int = -1):
        """设置文件的翻译进度"""
        self.data["files"][file_path] = {
            "completed": completed,
            "subtitle_index": subtitle_index
        }
        self.save()
        
    def get_file_progress(self, file_path: str) -> Dict:
        """获取文件的翻译进度"""
        return self.data["files"].get(file_path, {"completed": False, "subtitle_index": -1})
        
    def set_overall_progress(self, progress: float, current_file_index: int):
        """设置总体翻译进度"""
        self.data["overall_progress"] = progress
        self.data["current_file_index"] = current_file_index
        self.save()
        
    def get_overall_progress(self) -> Tuple[float, int]:
        """获取总体翻译进度和当前文件索引"""
        return self.data["overall_progress"], self.data["current_file_index"]


class SRTParser:
    """解析和生成SRT字幕文件的类"""
    
    @staticmethod
    def parse(srt_content: str) -> List[Dict]:
        """解析SRT内容为字典列表"""
        # 使用正则表达式匹配SRT格式
        pattern = re.compile(
            r'(\d+)\r?\n'
            r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\r?\n'
            r'(.*?)\r?\n\r?\n',
            re.DOTALL
        )
        
        matches = pattern.findall(srt_content)
        subtitles = []
        
        for match in matches:
            subtitle_id, start_time, end_time, text = match
            subtitles.append({
                'id': int(subtitle_id),
                'start_time': start_time,
                'end_time': end_time,
                'text': text.strip()
            })
            
        return subtitles
    
    @staticmethod
    def generate(subtitles: List[Dict], format_option: str = "only_chinese") -> str:
        """根据字幕字典列表生成SRT格式文本"""
        srt_lines = []
        
        for subtitle in subtitles:
            srt_lines.append(str(subtitle['id']))
            srt_lines.append(f"{subtitle['start_time']} --> {subtitle['end_time']}")
            
            # 根据格式选项处理文本
            if format_option == "only_chinese":
                srt_lines.append(subtitle['translated_text'])
            elif format_option == "chinese_top":
                srt_lines.append(f"{subtitle['translated_text']}\n{subtitle['original_text']}")
            elif format_option == "english_top":
                srt_lines.append(f"{subtitle['original_text']}\n{subtitle['translated_text']}")
                
            srt_lines.append('')  # 空行分隔
            
        return '\n'.join(srt_lines)


class ContextManager:
    """上下文管理器，用于保存和提供翻译上下文信息"""
    
    def __init__(self, context_window: int = 5):
        self.context_window = context_window  # 上下文窗口大小，每侧显示的字幕数量
        self.translation_history = []  # 保存历史翻译记录
        
    def add_translation(self, subtitle_id: int, original_text: str, translated_text: str):
        """添加翻译记录到历史中"""
        self.translation_history.append({
            "id": subtitle_id,
            "original": original_text,
            "translated": translated_text
        })
        
        # 限制历史记录长度，避免内存占用过大
        if len(self.translation_history) > self.context_window * 4:
            self.translation_history = self.translation_history[-self.context_window * 2:]
    
    def get_context_for_translation(self, current_id: int, all_subtitles: List[Dict]) -> str:
        """获取当前字幕的上下文信息"""
        # 找到当前字幕在列表中的索引
        current_index = next((i for i, sub in enumerate(all_subtitles) if sub['id'] == current_id), -1)
        
        if current_index == -1:
            return ""
            
        # 收集上下文字幕
        context = []
        
        # 添加之前的字幕作为上下文
        start_idx = max(0, current_index - self.context_window)
        for i in range(start_idx, current_index):
            sub = all_subtitles[i]
            # 检查是否已有翻译
            translated = next((t["translated"] for t in self.translation_history if t["id"] == sub['id']), None)
            
            if translated:
                context.append(f"前序字幕 #{sub['id']} (已翻译): {sub['text']} → {translated}")
            else:
                context.append(f"前序字幕 #{sub['id']}: {sub['text']}")
        
        # 添加后续的字幕作为上下文
        end_idx = min(len(all_subtitles) - 1, current_index + self.context_window)
        for i in range(current_index + 1, end_idx + 1):
            sub = all_subtitles[i]
            context.append(f"后续字幕 #{sub['id']}: {sub['text']}")
            
        if context:
            return "为了确保翻译的上下文一致性，参考以下相关字幕：\n" + "\n".join(context) + "\n\n"
        return ""


class DeepSeekTranslator:
    """使用DeepSeek API翻译文本的类（英文到中文），具备上下文感知能力"""
    
    def __init__(self, api_key: str, prompt: str = None, log_callback=None):
        """初始化翻译器"""
        self.api_key = api_key
        self.api_url = "https://api.deepseek.com/v1/chat/completions"
        self.prompt = prompt or "默认翻译提示词"
        self.log_callback = log_callback  # 日志回调函数，用于实时显示API信息
        self.context_manager = ContextManager()  # 上下文管理器
    
    def log(self, message):
        """记录日志，如果有回调函数则使用"""
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)
    
    @staticmethod
    def verify_api_key(api_key: str, log_callback=None) -> Tuple[bool, str]:
        """验证API密钥是否有效"""
        def log(message):
            if log_callback:
                log_callback(message)
            else:
                print(message)
                
        try:
            if not api_key:
                return False, "API密钥不能为空"
                
            log("正在验证API密钥...")
            url = "https://api.deepseek.com/v1/models"
            headers = {
                "Authorization": f"Bearer {api_key}"
            }
            
            log(f"发送验证请求到: {url}")
            response = requests.get(url, headers=headers, timeout=10)
            
            log(f"API验证响应状态码: {response.status_code}")
            if response.status_code == 200:
                return True, "API密钥验证成功"
            elif response.status_code == 401:
                return False, "API密钥无效或已过期"
            else:
                return False, f"验证失败，状态码: {response.status_code}"
                
        except requests.exceptions.Timeout:
            return False, "连接超时，请检查网络"
        except Exception as e:
            return False, f"验证出错: {str(e)}"
                
    def translate_text(self, text: str, subtitle_id: int, all_subtitles: List[Dict]) -> str:
        """翻译单段英文文本为中文，考虑上下文信息"""
        try:
            # 处理空文本
            if not text.strip():
                return text
            
            # 记录当前翻译的字幕ID
            subtitle_info = f"[字幕 #{subtitle_id}] "
            
            # 获取上下文信息
            context = self.context_manager.get_context_for_translation(subtitle_id, all_subtitles)
            if context:
                self.log(f"{subtitle_info}使用上下文信息辅助翻译")
            
            # 构建请求内容，包含上下文信息
            full_prompt = f"{self.prompt}\n{context}需要翻译的内容：{text}"
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            
            data = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": full_prompt}],
                "temperature": 0.7
            }
            
            # 发送请求并记录
            self.log(f"{subtitle_info}发送翻译请求...")
            response = requests.post(
                self.api_url,
                headers=headers,
                data=json.dumps(data),
                timeout=30
            )
            
            # 记录响应状态
            self.log(f"{subtitle_info}API响应状态码: {response.status_code}")
            
            # 处理响应
            if response.status_code == 200:
                result = response.json()
                translated_text = result['choices'][0]['message']['content'].strip()
                
                # 处理特殊字符
                translated_text = self._process_special_chars(translated_text)
                
                # 记录翻译结果（简略显示）
                preview = translated_text[:50] + ("..." if len(translated_text) > 50 else "")
                self.log(f"{subtitle_info}翻译完成: {preview}")
                
                # 将当前翻译添加到历史中，用于后续翻译的上下文
                self.context_manager.add_translation(subtitle_id, text, translated_text)
                
                return translated_text
            elif response.status_code == 401:
                raise Exception("API密钥无效或已过期")
            elif response.status_code == 429:
                raise Exception("请求过于频繁，请稍后再试")
            else:
                # 记录详细错误信息
                self.log(f"{subtitle_info}API错误响应: {response.text[:200]}")
                raise Exception(f"翻译失败，状态码: {response.status_code}")
                
        except Exception as e:
            self.log(f"{subtitle_info}翻译出错: {str(e)}")
            raise Exception(f"翻译出错: {str(e)}")
    
    def _process_special_chars(self, text: str) -> str:
        """处理特殊字符，按照指南替换"""
        # 双引号替换为单引号
        text = text.replace('"', "'")
        # 反斜杠替换为双反斜杠
        text = text.replace('\\', '\\\\')
        return text
    
    def translate_subtitles(self, subtitles: List[Dict], start_index: int = 0, 
                           progress_callback=None, stop_flag=None) -> List[Dict]:
        """翻译字幕列表，支持从指定索引开始（用于断点续译），考虑上下文"""
        total = len(subtitles)
        translated_subtitles = []
        
        # 如果有已翻译的字幕，先添加到结果中并更新上下文管理器
        for i in range(start_index):
            if i < len(subtitles):
                # 尝试从已有翻译中获取上下文
                existing_trans = next(
                    (t for t in translated_subtitles if t['id'] == subtitles[i]['id']), 
                    None
                )
                
                if existing_trans:
                    self.context_manager.add_translation(
                        subtitles[i]['id'], 
                        subtitles[i]['text'], 
                        existing_trans['translated_text']
                    )
                
                translated_subtitles.append({
                    'id': subtitles[i]['id'],
                    'start_time': subtitles[i]['start_time'],
                    'end_time': subtitles[i]['end_time'],
                    'original_text': subtitles[i]['text'],
                    'translated_text': ""  # 占位，后续会从文件加载
                })
        
        # 从指定索引开始翻译
        for i in range(start_index, total):
            # 检查是否需要停止
            if stop_flag and stop_flag.is_set():
                raise Exception("翻译已被用户中止")
                
            # 调用回调函数更新进度
            if progress_callback:
                overall_progress = (i / total) * 100
                progress_callback(overall_progress)
                
            # 翻译并传入字幕ID和所有字幕信息用于上下文处理
            translated_text = self.translate_text(
                subtitles[i]['text'], 
                subtitle_id=subtitles[i]['id'],
                all_subtitles=subtitles
            )
            
            translated_subtitles.append({
                'id': subtitles[i]['id'],
                'start_time': subtitles[i]['start_time'],
                'end_time': subtitles[i]['end_time'],
                'original_text': subtitles[i]['text'],
                'translated_text': translated_text
            })
            
            # 添加延迟避免请求过于频繁
            if i < total - 1:
                time.sleep(0.8)
            
        return translated_subtitles


class SRTTranslatorGUI:
    """SRT字幕翻译器的GUI界面类"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("SRT字幕翻译器")
        self.root.geometry("780x650")
        self.root.minsize(720, 600)
        
        # 变量初始化
        self.selected_files = []
        self.api_key = tk.StringVar()
        self.translation_thread = None
        self.stop_event = None
        self.current_file_index = 0
        self.total_files = 0
        
        # 初始化进度记录
        self.translation_progress = TranslationProgress()
        
        # 创建界面组件
        self.create_widgets()
        
        # 检查是否有未完成的翻译进度
        if self.translation_progress.load():
            overall_progress, current_file_idx = self.translation_progress.get_overall_progress()
            if overall_progress > 0 and overall_progress < 100:
                self.log(f"检测到未完成的翻译进度 (进度: {overall_progress:.1f}%)")
    
    def create_widgets(self):
        """创建GUI界面组件"""
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 顶部框架：API密钥和快速控制
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X, pady=(0, 10))
        
        # API密钥设置
        ttk.Label(top_frame, text="API密钥:").pack(side=tk.LEFT, padx=(0, 5))
        api_entry = ttk.Entry(top_frame, textvariable=self.api_key, width=40, show="*")
        api_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        # 添加API验证按钮
        ttk.Button(top_frame, text="验证密钥", command=self.verify_api_key).pack(side=tk.RIGHT, padx=(0, 5))
        
        # 提示词设置（使用新的专业提示词）
        prompt_frame = ttk.LabelFrame(main_frame, text="翻译提示词", padding="5")
        prompt_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.prompt_text = scrolledtext.ScrolledText(prompt_frame, height=6, wrap=tk.WORD)
        self.prompt_text.pack(fill=tk.X, expand=True)
        # 设置新的专业提示词
        self.prompt_text.insert(tk.END, """你是一位经验丰富的字幕翻译，曾为Netflix、HBO等知名流媒体平台翻译大量影视内容。擅长将英文字幕准确流畅地翻译成中文，并能根据不同影视作品的风格和语境，灵活使用正式或非正式、文雅或口语化的语言。只返回翻译结果，不要提供任何解释、分析或思考过程。如果根据场合、人物性格、年龄、影视剧题材等产生不同结果，请自行根据上下文决定采用。为了进一步提高翻译质量，你决定采用两遍翻译流程：
**翻译流程**：
1. **初步翻译**：首先对文本进行翻译，以捕捉原文中的所有重要信息。此阶段应侧重于提供全面且直接的翻译，确保信息传达的准确性。
2. **提炼与反思**：
   - **反思你的初步翻译**：回顾你的初步翻译，找出需要改进的地方。考虑以下几个方面：
     - **准确性**：核实原文中的所有细节和含义是否得以保留。纠正任何错误或遗漏。
     - **流畅度**：调整语法和标点符号，确保翻译在中文中读起来自然流畅。
     **风格**：确保翻译的风格和语气与原文内容相匹配，同时要符合目标文化背景。
     **术语**：在整个过程中使用一致且恰当的术语，以保持技术上的准确性和文化上的相关性。
   **最终翻译**：根据您的反馈，对初始翻译进行润色，以生成中文的流畅且口语化的版本。这一轮的修改应着重于提高译文对目标受众的自然度和吸引力。
请遵循以下指南：
**指南**：
- 确保当前这批字幕中的每个索引都与原文完全对应，且你的翻译应忠实反映每句的上下文。

- 利用上一批字幕、下一批字幕和历史批次中的上下文信息来指导你的翻译，但在输出结果中不要包含这些上下文信息。
- 根据原文的语气，适当使用非正式俚语和口语表达。
- 在翻译时，应优先考虑在目标语言中创建连贯且自然的文本，即使这意味着需要偏离原句结构或以不同的方式合并/拆分句子。
- 如果一个句子被拆分到多个字幕索引中，且这种拆分方式在目标语言中听起来不够自然，请随意调整翻译，以创造出听起来更自然的句子，同时确保整体意思不变。
- 不要拘泥于保持与原字幕相同数量的句子。如果合并或拆分句子能使译文更加连贯自然，那么就请这样做。
- 您可能会收到一些关于视频的额外背景信息以供参考，以及前一批和下一批的字幕。但是，您只需输出当前这一批的翻译。
- 如果原文包含其他语言，仍然只需在中文中输出译文。
- 您还将有选择地保留英文中的一些特定名称、技术术语或短语，特别在翻译一些涉及军事题材的影视剧时保留缩写名词，并使用单引号来表示它们不应被翻译。
- 在翻译这些特定术语时，请确保该术语的译文在同一行上。尽量避免将术语拆分成两行。
- 此外，输出字幕中的特殊字符、格式或标记应按如下方式处理：
  - (“) 双引号应替换为单引号(')
  - (\) 反斜杠应替换为 (\\)
**附加说明**：
- 考虑中文中的文化背景和习惯用法，以确保翻译不仅传达了原文的信息，还传达了原文的意图和语气。
- 这种方法旨在平衡准确性与文化相关性，确保翻译的高质量，既忠实于原文，又能吸引观众。请翻译以下内容：""")
        
        # 字幕格式选择
        format_frame = ttk.LabelFrame(main_frame, text="字幕格式", padding="5")
        format_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.format_var = tk.StringVar(value="only_chinese")
        
        # 使用水平布局
        format_inner = ttk.Frame(format_frame)
        format_inner.pack(fill=tk.X)
        
        ttk.Radiobutton(
            format_inner, 
            text="只显示中文", 
            variable=self.format_var, 
            value="only_chinese"
        ).pack(side=tk.LEFT, padx=(0, 15))
        
        ttk.Radiobutton(
            format_inner, 
            text="中文在上", 
            variable=self.format_var, 
            value="chinese_top"
        ).pack(side=tk.LEFT, padx=(0, 15))
        
        ttk.Radiobutton(
            format_inner, 
            text="英文在上", 
            variable=self.format_var, 
            value="english_top"
        ).pack(side=tk.LEFT)
        
        # 文件选择区域
        file_frame = ttk.LabelFrame(main_frame, text="文件列表", padding="5")
        file_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 文件选择按钮
        file_buttons = ttk.Frame(file_frame)
        file_buttons.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Button(file_buttons, text="添加文件", command=self.add_files).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(file_buttons, text="移除选中", command=self.remove_selected_file).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(file_buttons, text="清空", command=self.clear_file_list).pack(side=tk.LEFT)
        
        # 文件列表
        list_frame = ttk.Frame(file_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        list_scroll = ttk.Scrollbar(list_frame)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.file_listbox = Listbox(list_frame, yscrollcommand=list_scroll.set, selectmode=tk.EXTENDED, height=4)
        self.file_listbox.pack(fill=tk.BOTH, expand=True)
        list_scroll.config(command=self.file_listbox.yview)
        
        # 进度条区域
        progress_frame = ttk.LabelFrame(main_frame, text="进度", padding="5")
        progress_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 总体进度
        ttk.Label(progress_frame, text="总体:").pack(anchor=tk.W, pady=(0, 2))
        self.progress_bar = ttk.Progressbar(progress_frame, variable=tk.DoubleVar(), maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=(0, 5))
        
        # 当前文件进度
        ttk.Label(progress_frame, text="当前文件:").pack(anchor=tk.W, pady=(0, 2))
        self.file_progress_bar = ttk.Progressbar(progress_frame, variable=tk.DoubleVar(), maximum=100)
        self.file_progress_bar.pack(fill=tk.X, pady=(0, 5))
        
        self.progress_label = ttk.Label(progress_frame, text="准备就绪")
        self.progress_label.pack(anchor=tk.W)
        
        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="日志与API交互信息", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        log_scroll = ttk.Scrollbar(log_frame)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.log_text = tk.Text(log_frame, height=8, wrap=tk.WORD, yscrollcommand=log_scroll.set)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        log_scroll.config(command=self.log_text.yview)
        
        self.log_text.config(state=tk.DISABLED)
        
        # 底部按钮区域
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, anchor=tk.E)
        
        self.abort_button = ttk.Button(btn_frame, text="中止", command=self.abort_translation, state=tk.DISABLED)
        self.abort_button.pack(side=tk.RIGHT, padx=(0, 5))
        
        # 添加续译按钮
        self.resume_button = ttk.Button(btn_frame, text="续译", command=self.resume_translation, state=tk.DISABLED)
        self.resume_button.pack(side=tk.RIGHT, padx=(0, 5))
        
        ttk.Button(btn_frame, text="开始翻译", command=self.start_translation).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="退出", command=self.root.quit).pack(side=tk.RIGHT, padx=(0, 5))
        
        # 检查是否有可续译的进度
        self.check_resume_possible()
    
    def check_resume_possible(self):
        """检查是否有可续译的进度"""
        if self.translation_progress.load():
            overall_progress, current_file_idx = self.translation_progress.get_overall_progress()
            if overall_progress > 0 and overall_progress < 100 and self.selected_files:
                self.resume_button.config(state=tk.NORMAL)
                return True
        self.resume_button.config(state=tk.DISABLED)
        return False
    
    def verify_api_key(self):
        """验证API密钥"""
        api_key = self.api_key.get()
        
        if not api_key:
            messagebox.showerror("错误", "请输入API密钥")
            return
            
        self.log("正在验证API密钥...")
        
        # 在新线程中执行验证，避免UI冻结
        def verify_thread():
            success, msg = DeepSeekTranslator.verify_api_key(api_key, self.log)
            if success:
                self.root.after(0, lambda: messagebox.showinfo("成功", msg))
            else:
                self.root.after(0, lambda: messagebox.showerror("失败", msg))
        
        threading.Thread(target=verify_thread).start()
    
    def add_files(self):
        """添加多个SRT文件"""
        file_paths = filedialog.askopenfilenames(
            title="选择SRT文件",
            filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]
        )
        
        if file_paths:
            added = 0
            for path in file_paths:
                if path not in self.selected_files:
                    self.selected_files.append(path)
                    self.file_listbox.insert(END, os.path.basename(path))
                    added += 1
            if added > 0:
                self.log(f"已添加 {added} 个文件")
                self.check_resume_possible()
    
    def remove_selected_file(self):
        """移除选中的文件"""
        selected_indices = self.file_listbox.curselection()
        if not selected_indices:
            return
            
        # 从后往前删除，避免索引变化问题
        for i in sorted(selected_indices, reverse=True):
            del self.selected_files[i]
            self.file_listbox.delete(i)
        
        self.log(f"已移除 {len(selected_indices)} 个文件")
        self.check_resume_possible()
        # 如果删除了文件，清除进度记录
        self.translation_progress.clear()
    
    def clear_file_list(self):
        """清空文件列表"""
        count = len(self.selected_files)
        self.selected_files.clear()
        self.file_listbox.delete(0, END)
        if count > 0:
            self.log("已清空文件列表")
        self.check_resume_possible()
        # 清空文件列表时，清除进度记录
        self.translation_progress.clear()
                
    def log(self, message):
        """在日志区域添加消息，确保线程安全"""
        def update_log():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)  # 滚动到最后一行
            self.log_text.config(state=tk.DISABLED)
        
        # 使用after确保在主线程中更新UI
        self.root.after(0, update_log)
    
    def update_progress(self, value):
        """更新进度条和进度标签"""
        self.progress_bar["value"] = value
        self.root.update_idletasks()
    
    def update_file_progress(self, value, file_path: str):
        """更新当前文件的进度条，并保存进度"""
        # 计算当前文件在总体进度中的占比
        file_progress = (self.current_file_index / self.total_files) * 100
        file_contribution = (value / 100) * (100 / self.total_files)
        overall_progress = file_progress + file_contribution
        
        self.file_progress_bar["value"] = value
        self.progress_bar["value"] = overall_progress
        
        # 保存当前字幕索引（取整为完成的字幕数）
        total_subtitles = len(SRTParser.parse(read_file_with_encoding(file_path)[0]))
        completed_subtitles = int((value / 100) * total_subtitles)
        
        # 更新并保存进度
        self.translation_progress.set_file_progress(
            file_path, 
            value >= 100, 
            completed_subtitles
        )
        self.translation_progress.set_overall_progress(overall_progress, self.current_file_index)
        
        self.progress_label.config(
            text=f"处理 {self.current_file_index + 1}/{self.total_files} 个文件: {int(value)}%"
        )
        self.root.update_idletasks()
    
    def abort_translation(self):
        """中止正在进行的翻译"""
        if self.translation_thread and self.translation_thread.is_alive() and self.stop_event:
            self.stop_event.set()
            self.log("正在中止翻译...")
            self.abort_button.config(state=tk.DISABLED)
            # 中止后启用续译按钮
            self.root.after(1000, self.check_resume_possible)
    
    def start_translation(self, resume: bool = False):
        """开始翻译过程，resume=True表示从断点续译"""
        # 检查是否已有翻译线程在运行
        if self.translation_thread and self.translation_thread.is_alive():
            messagebox.showinfo("提示", "已有翻译任务在进行中")
            return
        
        # 验证输入
        if not self.selected_files:
            messagebox.showerror("错误", "请添加至少一个SRT文件")
            return
            
        api_key = self.api_key.get()
        if not api_key:
            messagebox.showerror("错误", "请输入DeepSeek API密钥")
            return
        
        # 验证API密钥
        self.log("正在验证API密钥...")
        success, msg = DeepSeekTranslator.verify_api_key(api_key, self.log)
        if not success:
            messagebox.showerror("API验证失败", msg)
            return
        
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            if messagebox.askyesno("提示", "提示词为空，是否使用默认提示词？"):
                # 使用内置的默认提示词
                prompt = """你是一位经验丰富的字幕翻译，擅长将英文字幕准确流畅地翻译成中文。请翻译以下内容："""
            else:
                return
        
        # 检查所有文件是否存在
        for file_path in self.selected_files:
            if not os.path.exists(file_path):
                messagebox.showerror("错误", f"文件不存在: {os.path.basename(file_path)}")
                return
        
        # 如果是新开始翻译，清除之前的进度记录
        if not resume:
            self.translation_progress.clear()
        else:
            self.log("从断点继续翻译...")
        
        # 准备开始翻译
        self.stop_event = threading.Event()
        self.abort_button.config(state=tk.NORMAL)
        self.resume_button.config(state=tk.DISABLED)
        
        # 获取起始索引
        start_index = 0
        if resume and self.translation_progress.load():
            _, start_index = self.translation_progress.get_overall_progress()
        
        self.current_file_index = start_index
        self.total_files = len(self.selected_files)
        
        # 在新线程中执行翻译，传入日志回调
        self.translation_thread = threading.Thread(
            target=self.perform_batch_translation,
            args=(api_key, prompt, self.format_var.get(), resume)
        )
        self.translation_thread.start()
    
    def resume_translation(self):
        """从断点继续翻译"""
        self.start_translation(resume=True)
    
    def perform_batch_translation(self, api_key: str, prompt: str, format_option: str, resume: bool):
        """批量翻译多个文件，支持从断点续译"""
        error_msg = None  # 保存错误信息
        try:
            # 清空日志和进度（仅新开始时）
            if not resume:
                self.root.after(0, lambda: self._reset_translation_ui())
            
            self.log(f"开始{'续' if resume else ''}译，共 {self.total_files} 个文件")
            
            # 逐个翻译文件（每个文件使用独立的翻译器实例，以保持上下文独立性）
            for i in range(self.current_file_index, self.total_files):
                if self.stop_event.is_set():
                    raise Exception("翻译已被用户中止")
                    
                self.current_file_index = i
                input_file = self.selected_files[i]
                filename = os.path.basename(input_file)
                self.log(f"\n处理文件 {i+1}/{self.total_files}: {filename}")
                
                # 为每个文件创建新的翻译器实例，确保上下文正确
                translator = DeepSeekTranslator(
                    api_key, 
                    prompt,
                    log_callback=self.log
                )
                
                # 处理单个文件，支持续译
                file_progress = self.translation_progress.get_file_progress(input_file) if resume else None
                self.process_single_file(input_file, translator, format_option, file_progress)
            
            # 翻译完成，清除进度记录
            self.translation_progress.clear()
            
            self.log("\n所有文件翻译完成！")
            self.root.after(0, lambda: messagebox.showinfo("成功", f"所有 {self.total_files} 个文件翻译完成！"))
            
        except Exception as e:
            error_msg = str(e)  # 保存错误信息
            self.log(f"\n发生错误: {error_msg}")
        finally:
            # 使用保存的错误信息
            if error_msg:
                self.root.after(0, lambda msg=error_msg: messagebox.showerror("错误", f"发生错误: {msg}"))
            self.root.after(0, self._translation_completed)
    
    def process_single_file(self, input_file: str, translator: DeepSeekTranslator, 
                           format_option: str, file_progress: Optional[Dict] = None):
        """处理单个SRT文件的翻译，支持从断点续译"""
        # 生成输出文件路径
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}_translated{ext}"
        
        # 读取SRT文件，尝试多种编码
        self.log(f"读取文件...")
        srt_content, encoding = read_file_with_encoding(input_file)
        self.log(f"文件编码: {encoding}")
        
        parser = SRTParser()
        subtitles = parser.parse(srt_content)
        
        if not subtitles:
            self.log("未找到有效的字幕内容，跳过此文件")
            self.translation_progress.set_file_progress(input_file, True, 0)
            return
        
        # 检查是否需要续译
        start_index = 0
        existing_translations = []
        
        if file_progress and not file_progress["completed"] and os.path.exists(output_file):
            # 从已翻译的文件中读取进度
            try:
                translated_content, _ = read_file_with_encoding(output_file)
                existing_translations = parser.parse(translated_content)
                start_index = file_progress["subtitle_index"] + 1  # 从下一个字幕开始
                if start_index >= len(subtitles):
                    start_index = 0
                
                self.log(f"从第 {start_index + 1} 条字幕继续翻译...")
            except Exception as e:
                self.log(f"读取已有翻译失败，将从头开始: {str(e)}")
                start_index = 0
        
        total_subtitles = len(subtitles)
        if start_index >= total_subtitles:
            self.log("该文件已全部翻译完成")
            self.translation_progress.set_file_progress(input_file, True, total_subtitles)
            self.update_file_progress(100, input_file)
            return
        
        self.log(f"找到 {total_subtitles} 条字幕，{'' if start_index == 0 else f'从第 {start_index + 1} 条开始'}翻译...")
        
        # 翻译字幕，从指定索引开始
        translated_subtitles = translator.translate_subtitles(
            subtitles, 
            start_index=start_index,
            progress_callback=lambda val: self.update_file_progress(val, input_file),
            stop_flag=self.stop_event
        )
        
        # 如果有已翻译的内容，合并它们
        if existing_translations and start_index > 0:
            # 合并已有的和新翻译的字幕
            final_translations = existing_translations[:start_index] + translated_subtitles[start_index:]
        else:
            final_translations = translated_subtitles
        
        # 生成并保存翻译后的SRT
        self.log("生成翻译后的文件...")
        translated_srt = parser.generate(final_translations, format_option)
        
        # 保存时使用UTF-8编码，确保兼容性
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(translated_srt)
        
        # 更新进度为完成
        self.translation_progress.set_file_progress(input_file, True, total_subtitles)
        self.log(f"已保存到: {os.path.basename(output_file)}")
        self.root.after(0, lambda: self.update_file_progress(100, input_file))
    
    def _reset_translation_ui(self):
        """重置翻译相关的UI元素"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.progress_bar["value"] = 0
        self.file_progress_bar["value"] = 0
        self.progress_label.config(text="准备就绪")
    
    def _translation_completed(self):
        """翻译完成或中止后恢复UI状态"""
        self.abort_button.config(state=tk.DISABLED)
        self.stop_event = None
        # 检查是否可以续译
        self.check_resume_possible()


if __name__ == "__main__":
    root = tk.Tk()
    # 确保中文显示正常
    root.option_add("*Font", ("SimHei", 9))
    app = SRTTranslatorGUI(root)
    root.mainloop()
    