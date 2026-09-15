# ai_service.py
import json
import logging

import requests
from docx import Document
from PyQt5.QtCore import QThread, pyqtSignal

from prompt_manager import load_prompt

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self, api_key: str, api_url: str = "https://api.deepseek.com"):
        self.api_key = api_key
        self.base_url = api_url  # 使用传入的api_url或默认值
        print(f"🔑 AI服务初始化，API密钥前8位: {api_key[:8]}...")
        print(f"🌐 API地址: {self.base_url}")

    def extract_text_from_docx(self, docx_path: str) -> str:
        """从Word文档中提取文本"""
        try:
            print(f"📖 正在读取Word文档: {docx_path}")
            doc = Document(docx_path)
            full_text = []

            for para in doc.paragraphs:
                if para.text.strip():  # 跳过空段落
                    full_text.append(para.text)

            result = "\n".join(full_text)
            print(f"✅ 提取文本成功，共{len(result)}字符")
            return result

        except Exception as e:
            logger.error(f"❌ 提取文本失败: {str(e)}")
            raise Exception(f"读取Word文档失败: {e}")

    def analyze_legal_document(self, document_text: str) -> dict:
        """分析法律文档"""
        try:
            print(f"📤 准备发送AI请求，文本长度: {len(document_text)}")

            # 构建提示词
            prompt = self._build_prompt(document_text)

            # 准备请求
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.3,
                "max_tokens": 2000
            }

            print(f"🌐 发送请求到: {self.base_url}/chat/completions")

            # 发送请求
            response = requests.post(
                f"{self.base_url}/chat/completions",  # ← 使用 self.base_url
                headers=headers,
                json=payload,
                timeout=60
            )

            print(f"📥 收到响应，状态码: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                print(f"✅ API调用成功")

                ai_response = result["choices"][0]["message"]["content"]
                print(f"📝 AI响应长度: {len(ai_response)}")
                print(f"📝 AI响应预览: {ai_response[:200]}...")

                return {
                    "审查状态": "完成",
                    "结果": ai_response,
                    "原始回复": ai_response
                }
            else:
                logger.error(f"❌ API错误: {response.status_code}")
                logger.error(f"❌ 错误详情: {response.text}")
                return {
                    "审查状态": "失败",
                    "错误信息": f"API调用失败: {response.status_code} - {response.text[:200]}"
                }

        except requests.exceptions.Timeout:
            print(f"⏰ 请求超时")
            return {
                "审查状态": "超时",
                "错误信息": "API请求超时，请检查网络连接"
            }
        except Exception as e:
            logger.error(f"❌ 请求异常: {str(e)}")
            return {
                "审查状态": "异常",
                "错误信息": f"请求异常: {str(e)}"
            }

    # 在 ai_service.py 中的 _build_prompt 方法修改
    def _build_prompt(self, document_text: str) -> str:
        """
        构建智能补问提示词（增强版）
        原来的常规审查 + 新增缺失问题识别
        """
        # 限制文本长度
        truncated_text = document_text[:2500] if len(document_text) > 2500 else document_text

        prompt = load_prompt('review').replace('{{笔录全文}}', truncated_text)

        return prompt

    def generate_injury_and_conclusion(self, transcript_text: str,
                                       regulation_text: str = "",
                                       regulation_desc: str = "",
                                       regulation_elements=None):
        """
        一次性从本人笔录生成两个结果：调查核实情况段落 + 简洁诊断结论

        :param transcript_text: 本人笔录全文
        :param regulation_text: 适用条款原文（如《工伤保险条例》第十四条第一款第一项）
        :param regulation_desc: 适用条款对应情形说明（如"在工作时间和工作场所内，因工作原因受到事故伤害"）
        :param regulation_elements: 该条款须突出的关键证据要素（如三工要素）
        :return: {"受伤经过": str, "诊断结论": str}，失败返回 None
                （"受伤经过"字段即认定工伤决定书的"调查核实情况"段落）
        """
        try:
            if not transcript_text or not transcript_text.strip():
                return None

            truncated = transcript_text[:4000] if len(transcript_text) > 4000 else transcript_text

            # ── 组装条款情形说明（供 AI 突出关键证据要素）──
            clause_lines = []
            if regulation_text:
                clause_lines.append(f"本案拟适用的条款：{regulation_text}")
            if regulation_desc:
                clause_lines.append(f"该条款对应情形：{regulation_desc}")
            if regulation_elements:
                clause_lines.append("该条款要求突出以下关键证据要素：" + "、".join(regulation_elements))
            clause_block = "\n".join(clause_lines)

            if clause_block:
                clause_injection = (
                    "结合本案拟适用的条款情形，如下：\n" + clause_block +
                    "\n请在事实描述中自然嵌入上述关键证据要素，确保证据要素在事实描述中清晰可辨。\n"
                )
            else:
                clause_injection = ""

            prompt = load_prompt('injury_and_conclusion').replace('{{条款信息}}', clause_injection).replace('{{笔录全文}}', truncated)

            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }

            data = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 2000
            }

            print(f"📤 发送受伤事实+诊断结论生成请求，笔录长度: {len(transcript_text)}")

            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content'].strip()

                # 解析：以【诊断结论】为唯一分界，其前为调查核实情况段落，其后为诊断结论
                injury = ""
                conclusion = ""
                m2 = content.find("【诊断结论】")
                if m2 != -1:
                    injury = content[:m2].strip()
                    conclusion = content[m2 + len("【诊断结论】"):].strip()
                else:
                    injury = content.strip()

                # 清理段落开头可能残留的标题
                for header in ("【调查核实情况】", "调查核实情况：", "调查核实情况:", "调查核实情况"):
                    if injury.startswith(header):
                        injury = injury[len(header):].strip()
                        break

                # 清理诊断结论（去括号、去前缀、去多余文字）
                conclusion = conclusion.replace("【", "").replace("】", "").strip()
                for prefix in ["诊断结论：", "医疗诊断结论：", "结论："]:
                    if conclusion.startswith(prefix):
                        conclusion = conclusion[len(prefix):].strip()

                print(f"✅ 生成完成：调查核实情况 {len(injury)}字，诊断结论「{conclusion}」")
                return {"受伤经过": injury, "诊断结论": conclusion}
            else:
                logger.error(f"❌ 生成失败，状态码: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"❌ 生成失败: {e}")
            return None

    def analyze_approval_transcripts(self, transcripts_text: str,
                                     case_id: str = "",
                                     regulation_text: str = "",
                                     regulation_elements=None):
        """
        AI 分析全部谈话笔录（本人/证人/法人）→ 判断认定/不予认定偏向。

        Returns:
            {"偏向": "认定"/"不予认定", "分析": str, "关键理由": [str], "诊断结论": str}
            失败返回 None
        """
        try:
            if not transcripts_text or not transcripts_text.strip():
                return None

            from prompt_manager import load_prompt
            prompt = load_prompt('approval_analysis')
            elements = "、".join(regulation_elements) if regulation_elements else ""
            prompt = prompt.replace('{{案本号}}', str(case_id or ""))
            prompt = prompt.replace('{{拟用条例}}', str(regulation_text or ""))
            prompt = prompt.replace('{{法律要件}}', elements)
            prompt = prompt.replace('{{全部笔录}}', transcripts_text[:6000])

            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            data = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            }

            print(f"📤 发送审批表分析请求，笔录 {len(transcripts_text)} 字符")
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=120,
            )

            if response.status_code == 200:
                content = response.json()["choices"][0]["message"]["content"]
                parsed = self._parse_json_response(content)
                if isinstance(parsed, dict) and '偏向' in parsed:
                    print(f"✅ 审批表分析完成，偏向: {parsed.get('偏向')}")
                    return parsed
                return None
            else:
                logger.error(f"❌ 审批表分析失败，状态码: {response.status_code}")
                return None

        except requests.exceptions.Timeout:
            print("⏰ 审批表分析请求超时")
            return None
        except Exception as e:
            logger.error(f"❌ 审批表分析异常: {e}")
            return None

    def generate_transcript(self, system_prompt: str, user_prompt: str) -> dict:
        """
        调用 DeepSeek API 生成谈话笔录问答内容。

        使用 System + User 双消息格式：
        - System Prompt 固化法律知识（可被 DeepSeek 缓存，不计入 Token）
        - User Prompt 传入个案事实（约 300 Token）

        Args:
            system_prompt: 法律规范的 System Prompt
            user_prompt: 个案事实的 User Prompt

        Returns:
            {"状态": "成功"/"失败", "内容": str, "错误信息": str}
        """
        try:
            print(f"📤 准备生成谈话笔录")
            print(f"   System Prompt 长度: {len(system_prompt)} 字符")
            print(f"   User Prompt 长度: {len(user_prompt)} 字符")

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
                "top_p": 0.9,
            }

            print(f"🌐 发送请求到: {self.base_url}/chat/completions")

            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120  # 生成笔录允许更长的超时
            )

            print(f"📥 收到响应，状态码: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                content = result["choices"][0]["message"]["content"]

                # 获取 Token 用量信息
                usage = result.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)

                print(f"✅ 笔录生成成功")
                print(f"   Prompt Tokens: {prompt_tokens}")
                print(f"   Completion Tokens: {completion_tokens}")
                print(f"   Total Tokens: {total_tokens}")
                print(f"   内容长度: {len(content)} 字符")

                return {
                    "状态": "成功",
                    "内容": content.strip(),
                    "用量": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    }
                }
            else:
                error_msg = f"API 返回错误 {response.status_code}: {response.text[:300]}"
                logger.error(f"❌ {error_msg}")
                return {
                    "状态": "失败",
                    "内容": "",
                    "错误信息": error_msg
                }

        except requests.exceptions.Timeout:
            print("⏰ 请求超时（120秒）")
            return {
                "状态": "超时",
                "内容": "",
                "错误信息": "API请求超时，请检查网络连接后重试"
            }
        except Exception as e:
            logger.error(f"❌ 请求异常: {str(e)}")
            return {
                "状态": "异常",
                "内容": "",
                "错误信息": f"请求异常: {str(e)}"
            }

    def generate_transcript_from_text(self, full_text: str) -> dict:
        """把「发送给AI的模板」渲染后的全文发给 AI，生成询问笔录。

        全文已包含角色设定、案件信息与任务指令，故作为 user prompt 一次性发送。
        """
        return self.generate_transcript("", full_text)

    @staticmethod
    def _parse_json_response(content: str) -> dict:
        """解析 AI 返回的 JSON（兼容 markdown 代码块包裹）"""
        text = (content or "").strip()
        if text.startswith("```"):
            lines = [l for l in text.split("\n") if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"⚠️ JSON 解析失败，尝试正则提取: {e}")
            import re
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
        return {"错误": "无法解析AI返回的JSON", "原始": (content or "")[:500]}

# ============================================================================
# 后台线程包装（把阻塞的 API 调用丢出界面线程）
# ----------------------------------------------------------------------------
# 原先定义在 app_main.py 里（2026-09 搬过来）。它们只依赖 AIService 的接口，
# 放这里是为了跟被包装的服务挨着；代价是本模块从「纯 requests」变成依赖 PyQt5
# ——本项目只有 app_main 用它，没有别的入口会因此被拖进 Qt。
# ============================================================================

class AIWorker(QThread):
    """AI工作线程"""
    finished = pyqtSignal(dict)  # 发送完成信号
    error = pyqtSignal(str)  # 发送错误信号
    progress = pyqtSignal(str, int)  # 发送进度信号 (消息, 进度百分比)

    def __init__(self, ai_service, file_path):
        super().__init__()
        self.ai_service = ai_service
        self.file_path = file_path

    def run(self):
        """线程运行的主函数"""
        try:
            # 第一步：提取文本
            self.progress.emit("正在提取文档文本...", 20)
            document_text = self.ai_service.extract_text_from_docx(self.file_path)

            # 第二步：AI分析
            self.progress.emit("正在调用DeepSeek API进行分析...", 50)
            result = self.ai_service.analyze_legal_document(document_text)

            # 第三步：完成
            self.progress.emit("分析完成，正在生成报告...", 90)
            self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))


class TranscriptFromTemplateWorker(QThread):
    """把发给 AI 的提示词文本转发给 AIService 生成谈话笔录的后台线程（本人/证人/法人共用）"""
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, ai_service, full_text):
        super().__init__()
        self.ai_service = ai_service
        self.full_text = full_text

    def run(self):
        try:
            result = self.ai_service.generate_transcript_from_text(self.full_text)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ============================================================================
# 解析 AI 回复
# ----------------------------------------------------------------------------
# 原先挂在 MainWindow 上（2026-09 搬来）。它不碰 self，解析的又正是本模块
# 约定的回复格式（【审查结果】/【缺失问题列表】），所以归这里。
# ============================================================================

def parse_ai_result(ai_text: str) -> dict:
    """解析AI结果，提取审查结果和缺失问题

    Args:
        ai_text: AI返回的完整文本

    Returns:
        包含审查结果和缺失问题的字典
    """
    # None / 非字符串一律当空文本：以前 None 会让末尾那句 print 的 len(None)
    # 抛 TypeError——而 {"结果": None} 这种输入是够得着的。
    if ai_text is None:
        ai_text = ""
    elif not isinstance(ai_text, str):
        ai_text = str(ai_text)

    result = {
        "审查结果": "",
        "缺失问题": [],
        "原始文本": ai_text
    }

    try:
        # 分割审查结果和缺失问题
        if "【审查结果】" in ai_text and "【缺失问题列表】" in ai_text:
            # 提取审查结果部分
            start = ai_text.find("【审查结果】")
            end = ai_text.find("【缺失问题列表】")

            if start != -1 and end != -1:
                review_text = ai_text[start:end]
                # 清理标记
                review_text = review_text.replace("【审查结果】", "").strip()
                result["审查结果"] = review_text

                # 提取缺失问题部分
                questions_text = ai_text[end:]
                # 按行分割
                lines = questions_text.split('\n')

                for line in lines:
                    line = line.strip()
                    # 查找带方框的问题行
                    if "□" in line and "问：" in line:
                        # 提取问题文本（去掉方框和序号）
                        # 示例：□ 1. 问：您与公司是否签订了书面劳动合同？
                        question = line
                        # 去掉方框标记
                        question = question.replace("□", "", 1).strip()
                        # 去掉序号（如"1. "）
                        if "." in question:
                            question = question.split(".", 1)[1].strip()

                        result["缺失问题"].append(question)

        # 如果格式不正确，尝试其他解析方式
        elif "审查结果" in ai_text and "缺失问题" in ai_text:
            # 尝试其他格式解析
            pass

        else:
            # 如果没有找到格式标记，整个文本作为审查结果
            result["审查结果"] = ai_text

    except Exception as e:
        print(f"解析AI结果失败: {e}")
        result["审查结果"] = ai_text

    print(f"✅ 解析结果: 审查结果长度={len(result['审查结果'])}, 问题数量={len(result['缺失问题'])}")
    return result
