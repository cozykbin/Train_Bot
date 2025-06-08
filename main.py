import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
from datetime import datetime, timedelta, time
from pytz import timezone
import asyncio
import os

# -----------------------------------------------------------------------------
# 환경 변수 로드 및 봇 초기화
# -----------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True        # 회원 정보 조회
intents.voice_states = True   # 음성 채널 입퇴장 감지
intents.guilds = True         # 포럼 스레드 감지
bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------------------------------------------------------
# 헬퍼 함수: KST 시간 및 푸터 텍스트 생성
# -----------------------------------------------------------------------------
def get_kst_now() -> datetime:
    """Asia/Seoul 시간으로 현재 datetime을 반환합니다."""
    return datetime.now(timezone("Asia/Seoul"))

def format_footer(user: discord.User) -> dict:
    """
    '닉네임 | 오늘/어제/YY/MM/DD, HH:MM' 형식의 텍스트와
    프로필 아이콘 URL을 반환합니다.
    """
    now = get_kst_now()
    today = now.date()
    yesterday = today - timedelta(days=1)

    if now.date() == today:
        label = "오늘"
    elif now.date() == yesterday:
        label = "어제"
    else:
        label = now.strftime("%y/%m/%d")

    time_label = now.strftime("%H:%M")
    display_name = user.display_name
    avatar_url = user.display_avatar.url if hasattr(user, "display_avatar") else user.avatar.url

    return {
        "text": f"{display_name} | {label}, {time_label}",
        "icon_url": avatar_url
    }

# -----------------------------------------------------------------------------
# 전역: 사용자별 목표 & 진행 로그 저장소 (데모용, 실제 서비스 시 DB 사용 권장)
# -----------------------------------------------------------------------------
# user_goals 구조 예시:
# user_goals = {
#   "user_id_str": {
#       "weight_goal": {
#           "weeks": int,
#           "start_weight": float,
#           "target_weight": float,
#           "achieved": bool,
#           "progress_pct": int
#       },
#       "frequency_goal": {
#           "per_week": int,
#           "achieved_this_week": int
#       },
#       "diet_goal": {
#           "per_week": int,
#           "achieved_this_week": int
#       },
#       "badges": {
#           "weekly_badges": int,
#           "monthly_trophies": int,
#           "bikinis": int
#       },
#       "weekly_log": { "월": bool, "화": bool, "수": bool, "목": bool, "금": bool },
#       "voice_session": { "start": datetime or None }  # 음성 채널 운동 추적용
#   }
# }
user_goals: dict[str, dict] = {}

# 주간 체중 DM 흐름 관리용
# weight_dm_context = {
#   "user_id_str": {"stage": int, "weeks": int, "start_weight": float}
# }
weight_dm_context: dict[str, dict] = {}

# -----------------------------------------------------------------------------
# 트래킹 채널 정보 (서버 환경에 맞게 변경하세요)
# -----------------------------------------------------------------------------
TRACKED_VOICE_CHANNELS = ["🏋🏻｜헬스장", "🏋🏻｜헬스장2"]  # 15분 이상 머무르면 운동 인증
FORUM_CHANNEL_ID = 1379409429597786112  # “식단인증” 포럼 채널 ID 

# -----------------------------------------------------------------------------
# 1) 메인 메뉴: !쌤 커맨드 → 인삿말 + 프로필 이미지 임베드 + 세 가지 버튼
# -----------------------------------------------------------------------------
class MainMenuView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎯 목표설정", style=discord.ButtonStyle.primary, custom_id="goal_settings")
    async def on_goal_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        """목표설정 버튼 클릭 시: 운동/식단 목표 설정 메뉴로 임베드 및 버튼 갱신"""
        embed = discord.Embed(
            title="🏆 운동/식단 목표 설정",
            description=(
                "“🏋️ 운동목표 설정” 또는 “🍽️ 식단목표 설정”을 선택하세요.\n\n"
                "`🔙 뒤로가기` 버튼을 누르면 메인 메뉴로 돌아갑니다."
            ),
            color=discord.Color.blue()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = GoalTypeView()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="📊 기록확인", style=discord.ButtonStyle.success, custom_id="view_progress")
    async def on_view_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        """기록확인 버튼 클릭 시: 사용자의 목표/진행현황 임베드 표시"""
        user_id = str(interaction.user.id)
        footer = format_footer(interaction.user)

        if user_id not in user_goals or (
            "weight_goal" not in user_goals[user_id]
            and "frequency_goal" not in user_goals[user_id]
            and "diet_goal" not in user_goals[user_id]
        ):
            embed = discord.Embed(
                title="📊 기록 확인",
                description="아직 목표를 설정하지 않으셨습니다. 먼저 **목표설정**을 해주세요! 🐹",
                color=discord.Color.yellow()
            )
            embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
            return await interaction.response.edit_message(embed=embed, view=MainMenuView())

        data = user_goals[user_id]
        embed = discord.Embed(title="📊 현재 진행 현황", color=discord.Color.green())

        # 1) ⚖️ 체중 감량 목표 현황
        if "weight_goal" in data:
            wg = data["weight_goal"]
            total_weeks = wg["weeks"]
            progress_pct = wg.get("progress_pct", 0)
            bikini_badge = wg.get("achieved", False)
            embed.add_field(
                name="⚖️ 체중 감량 목표",
                value=(
                    f"• 기간: {total_weeks}주\n"
                    f"• 진행률: {progress_pct}%\n"
                    f"• 비키니 배지: {'👙' if bikini_badge else '❌'}"
                ),
                inline=False
            )
        else:
            embed.add_field(name="⚖️ 체중 감량 목표", value="설정되지 않음", inline=False)

        # 2) 🏋️‍♂️ 주당 운동 횟수 목표 현황
        if "frequency_goal" in data:
            fg = data["frequency_goal"]
            per_week = fg["per_week"]
            achieved = fg.get("achieved_this_week", 0)
            remaining_days = max(0, 5 - sum(data.get("weekly_log", {}).values()))
            status = "⭕" if achieved >= per_week else "❌"
            embed.add_field(
                name="🏋️‍♂️ 주당 운동 횟수 목표",
                value=(
                    f"• 목표: {per_week}회\n"
                    f"• 달성: {achieved}회\n"
                    f"• 남은 일(월~금): {remaining_days}\n"
                    f"• 달성 여부: {status}"
                ),
                inline=False
            )
        else:
            embed.add_field(name="🏋️‍♂️ 주당 운동 횟수 목표", value="설정되지 않음", inline=False)

        # 3) 🍎 주당 식단 인증 목표 현황
        if "diet_goal" in data:
            dg = data["diet_goal"]
            per_week = dg["per_week"]
            achieved = dg.get("achieved_this_week", 0)
            remaining_days = max(0, 5 - sum(data.get("weekly_log", {}).values()))
            status = "⭕" if achieved >= per_week else "❌"
            embed.add_field(
                name="🍎 주당 식단 인증 목표",
                value=(
                    f"• 목표: {per_week}회\n"
                    f"• 달성: {achieved}회\n"
                    f"• 남은 일(월~금): {remaining_days}\n"
                    f"• 달성 여부: {status}"
                ),
                inline=False
            )
        else:
            embed.add_field(name="🍎 주당 식단 인증 목표", value="설정되지 않음", inline=False)

        # 4) 📅 이번주 월~금 진행현황
        if "weekly_log" in data:
            wl = data["weekly_log"]
            weekday_names = ["월", "화", "수", "목", "금"]
            symbols = [("⭕" if wl.get(day, False) else "❌") for day in weekday_names]
            text = "\n".join([f"• {d}: {s}" for d, s in zip(weekday_names, symbols)])
            embed.add_field(name="📅 이번주 진행현황 (월~금)", value=text, inline=False)
        else:
            embed.add_field(name="📅 이번주 진행현황 (월~금)", value="기록 없음", inline=False)

        # 5) 🎗️ 배지 현황
        badges = data.get("badges", {"weekly_badges": 0, "bikinis": 0, "monthly_trophies": 0})
        embed.add_field(
            name="🎗️ 배지 현황",
            value=(
                f"• 훈장(주간 달성): {badges['weekly_badges']}개\n"
                f"• 비키니(체중 달성): {badges['bikinis']}개\n"
                f"• 트로피(월간 완주): {badges['monthly_trophies']}개"
            ),
            inline=False
        )

        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        await interaction.response.edit_message(embed=embed, view=MainMenuView())

    @discord.ui.button(label="💪🏻 근육랭킹", style=discord.ButtonStyle.secondary, custom_id="muscle_ranking")
    async def on_muscle_ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        """근육랭킹 버튼 클릭 시: 배지/운동/식단 순위 임베드 표시"""
        footer = format_footer(interaction.user)

        ranking_data = []
        for uid, data in user_goals.items():
            # 길드 내 Member 객체 시도
            if interaction.guild:
                member_obj = interaction.guild.get_member(int(uid))
                name = member_obj.display_name if member_obj else f"사용자({uid})"
            else:
                name = f"사용자({uid})"

            badges = data.get("badges", {"weekly_badges": 0, "bikinis": 0, "monthly_trophies": 0})
            exercise_count = data.get("frequency_goal", {}).get("achieved_this_week", 0)
            diet_count = data.get("diet_goal", {}).get("achieved_this_week", 0)
            total_badges = badges["weekly_badges"] + badges["bikinis"] + badges["monthly_trophies"]

            ranking_data.append({
                "name": name,
                "badges": total_badges,
                "exercise": exercise_count,
                "diet": diet_count
            })

        # 배지 Top 5
        top_badges = sorted(ranking_data, key=lambda x: x["badges"], reverse=True)[:5]
        # 운동 Top 5
        top_exercise = sorted(ranking_data, key=lambda x: x["exercise"], reverse=True)[:5]
        # 식단 Top 5
        top_diet = sorted(ranking_data, key=lambda x: x["diet"], reverse=True)[:5]

        embed = discord.Embed(title="💪🏻 근육랭킹", color=discord.Color.purple())

        description_badges = "\n".join(
            [f"{i+1}위 🏅 **{entry['name']}** — 배지 {entry['badges']}개"
             for i, entry in enumerate(top_badges)]
        ) or "배지 데이터가 없습니다."
        embed.add_field(name="🥇 배지 Top 5", value=description_badges, inline=False)

        description_ex = "\n".join(
            [f"{i+1}위 💪🏻 **{entry['name']}** — 운동 {entry['exercise']}회"
             for i, entry in enumerate(top_exercise)]
        ) or "운동 데이터가 없습니다."
        embed.add_field(name="🔥 이번 주 운동 Top 5", value=description_ex, inline=False)

        description_dt = "\n".join(
            [f"{i+1}위 🍖 **{entry['name']}** — 식단 {entry['diet']}회"
             for i, entry in enumerate(top_diet)]
        ) or "식단 데이터가 없습니다."
        embed.add_field(name="🥗 이번 주 식단 Top 5", value=description_dt, inline=False)

        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        await interaction.response.edit_message(embed=embed, view=MainMenuView())


# -----------------------------------------------------------------------------
# 2) 목표 유형 선택 메뉴: 운동 목표 vs 식단 목표 vs 뒤로가기
# -----------------------------------------------------------------------------
class GoalTypeView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🏋️ 운동목표 설정", style=discord.ButtonStyle.primary, custom_id="choose_exercise_goal")
    async def on_choose_exercise_goal(self, interaction: discord.Interaction, button: discord.ui.Button):
        """운동 목표 선택 시: 체중/횟수 목표 세부 메뉴로 이동"""
        embed = discord.Embed(
            title="🏋️ 운동 목표 선택",
            description=(
                "1️⃣ ⚖️ 체중 감량 목표\n"
                "2️⃣ 🌐 주당 운동 횟수 목표\n\n"
                "`🔙 뒤로가기` 버튼을 누르면 이전으로 돌아갑니다."
            ),
            color=discord.Color.orange()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = ExerciseGoalView()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="🍽️ 식단목표 설정", style=discord.ButtonStyle.success, custom_id="choose_diet_goal")
    async def on_choose_diet_goal(self, interaction: discord.Interaction, button: discord.ui.Button):
        """식단 목표 선택 시: 1~7 선택 버튼 메뉴로 이동"""
        embed = discord.Embed(
            title="🍎 식단 목표 설정",
            description=(
                "이번 주 몇 회 식단 인증할까요? (1~7)\n\n"
                "`🔙 뒤로가기` 버튼을 누르면 이전으로 돌아갑니다."
            ),
            color=discord.Color.teal()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = DietGoalView()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="🔙 뒤로가기", style=discord.ButtonStyle.danger, custom_id="back_to_main_from_goal")
    async def on_back_to_main_from_goal(self, interaction: discord.Interaction, button: discord.ui.Button):
        """뒤로가기 → 메인 메뉴로 돌아감"""
        embed = discord.Embed(
            title="🐹 트레이너 햄찌봇",
            description=(
                "안녕하세요! 트레이너 햄찌봇입니다.\n아래 버튼을 눌러주세요:\n\n"
                "🎯 **목표설정**  |  📊 **기록확인**  |  💪🏻 **근육랭킹**"
            ),
            color=discord.Color.blurple()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)


# -----------------------------------------------------------------------------
# 2-1) 운동 목표 세부 메뉴: 체중 감량 vs 주당 횟수 vs 뒤로가기
# -----------------------------------------------------------------------------
class ExerciseGoalView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⚖️ 체중 감량 목표", style=discord.ButtonStyle.secondary, custom_id="weight_loss_goal")
    async def on_weight_loss_goal(self, interaction: discord.Interaction, button: discord.ui.Button):
        """체중 감량 목표 설정 → DM으로 3단계 입력 요청"""
        user_id = str(interaction.user.id)
        if user_id not in user_goals:
            # 최초 구조 초기화
            user_goals[user_id] = {
                "badges": {"weekly_badges": 0, "bikinis": 0, "monthly_trophies": 0},
                "weekly_log": {},
                "voice_session": {}
            }

        # DM 컨텍스트 초기화
        weight_dm_context[user_id] = {"stage": 1}
        await interaction.response.send_message(
            content="✅ DM으로 **체중 감량 목표** 정보를 요청드릴게요! DM을 확인해주세요. 📨",
            ephemeral=True
        )
        try:
            dm_channel = await interaction.user.create_dm()
            await dm_channel.send(
                "⚖️ **체중 감량 목표 설정**을 시작합니다.\n"
                "1️⃣ 먼저, 몇 주 동안 목표를 달성할 예정인가요? (숫자만 입력해주세요)\n\n"
                "_(입력하신 체중 정보는 서버에 저장되지 않으며, “진행률”만 관리됩니다.)_"
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ DM을 열 수 없습니다. DM 설정을 허용해주세요.", ephemeral=True)

    @discord.ui.button(label="🌐 주당 운동 횟수 목표", style=discord.ButtonStyle.primary, custom_id="frequency_goal")
    async def on_frequency_goal(self, interaction: discord.Interaction, button: discord.ui.Button):
        """주당 운동 횟수 목표 설정 → 숫자(1~7) 버튼 메뉴로 이동"""
        user_id = str(interaction.user.id)
        if user_id not in user_goals:
            user_goals[user_id] = {
                "badges": {"weekly_badges": 0, "bikinis": 0, "monthly_trophies": 0},
                "weekly_log": {},
                "voice_session": {}
            }

        embed = discord.Embed(
            title="🌐 주당 운동 목표 설정",
            description="이번 주에 **몇 회** 운동할 계획인가요? (1~7)\n\n"
                        "`🔙 뒤로가기` 버튼을 누르면 이전으로 돌아갑니다.",
            color=discord.Color.orange()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = FrequencyGoalView()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="🔙 뒤로가기", style=discord.ButtonStyle.danger, custom_id="back_to_goal_from_exercise")
    async def on_back_to_goal_from_exercise(self, interaction: discord.Interaction, button: discord.ui.Button):
        """뒤로가기 → 목표 유형 선택으로 복귀"""
        embed = discord.Embed(
            title="🏆 운동/식단 목표 설정",
            description=(
                "“🏋️ 운동목표 설정” 또는 “🍽️ 식단목표 설정”을 선택하세요.\n\n"
                "`🔙 뒤로가기` 버튼을 누르면 메인 메뉴로 돌아갑니다."
            ),
            color=discord.Color.blue()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = GoalTypeView()
        await interaction.response.edit_message(embed=embed, view=view)


# -----------------------------------------------------------------------------
# 2-1-1) 주당 운동 횟수 목표 설정 메뉴: 1~7 숫자 버튼 + 뒤로가기
# -----------------------------------------------------------------------------
class FrequencyGoalView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def handle_frequency_selection(self, interaction: discord.Interaction, times: int):
        """사용자가 숫자 버튼을 클릭했을 때, 주당 운동 목표 저장 및 안내"""
        user_id = str(interaction.user.id)
        data = user_goals[user_id]
        data["frequency_goal"] = {"per_week": times, "achieved_this_week": 0}
        # 주간 로그 초기화(월~금)
        data.setdefault("weekly_log", {})
        for wd in ["월", "화", "수", "목", "금"]:
            data["weekly_log"][wd] = False

        embed = discord.Embed(
            title="✅ 주당 운동 목표 설정 완료!",
            description=(f"이번 주 운동 목표는 **{times}회**입니다.\n"
                         "필요에 따라 언제든 목표를 수정할 수 있어요. (다시 목표설정 → 주당 운동 목표)"),
            color=discord.Color.orange()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="1", style=discord.ButtonStyle.primary, custom_id="freq_1")
    async def on_freq_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_frequency_selection(interaction, 1)
    @discord.ui.button(label="2", style=discord.ButtonStyle.primary, custom_id="freq_2")
    async def on_freq_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_frequency_selection(interaction, 2)
    @discord.ui.button(label="3", style=discord.ButtonStyle.primary, custom_id="freq_3")
    async def on_freq_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_frequency_selection(interaction, 3)
    @discord.ui.button(label="4", style=discord.ButtonStyle.primary, custom_id="freq_4")
    async def on_freq_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_frequency_selection(interaction, 4)
    @discord.ui.button(label="5", style=discord.ButtonStyle.primary, custom_id="freq_5")
    async def on_freq_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_frequency_selection(interaction, 5)
    @discord.ui.button(label="6", style=discord.ButtonStyle.primary, custom_id="freq_6")
    async def on_freq_6(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_frequency_selection(interaction, 6)
    @discord.ui.button(label="7", style=discord.ButtonStyle.primary, custom_id="freq_7")
    async def on_freq_7(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_frequency_selection(interaction, 7)

    @discord.ui.button(label="🔙 뒤로가기", style=discord.ButtonStyle.danger, custom_id="back_to_exercise_from_freq")
    async def on_back_to_exercise_from_freq(self, interaction: discord.Interaction, button: discord.ui.Button):
        """뒤로가기 → 운동 목표 선택으로 복귀"""
        embed = discord.Embed(
            title="🏋️ 운동 목표 선택",
            description=(
                "1️⃣ ⚖️ 체중 감량 목표\n"
                "2️⃣ 🌐 주당 운동 횟수 목표\n\n"
                "`🔙 뒤로가기` 버튼을 누르면 이전으로 돌아갑니다."
            ),
            color=discord.Color.orange()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = ExerciseGoalView()
        await interaction.response.edit_message(embed=embed, view=view)


# -----------------------------------------------------------------------------
# 2-2) 식단 목표 설정 메뉴: 1~7 숫자 버튼 + 뒤로가기
# -----------------------------------------------------------------------------
class DietGoalView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def handle_diet_selection(self, interaction: discord.Interaction, days: int):
        """사용자가 숫자 버튼을 클릭했을 때, 식단 목표 저장 및 안내"""
        user_id = str(interaction.user.id)
        if user_id not in user_goals:
            user_goals[user_id] = {
                "badges": {"weekly_badges": 0, "bikinis": 0, "monthly_trophies": 0},
                "weekly_log": {},
                "voice_session": {}
            }

        data = user_goals[user_id]
        data["diet_goal"] = {"per_week": days, "achieved_this_week": 0}
        # 주간 로그 초기화(월~금)
        data.setdefault("weekly_log", {})
        for wd in ["월", "화", "수", "목", "금"]:
            data["weekly_log"][wd] = False

        embed = discord.Embed(
            title="✅ 식단 목표 설정 완료!",
            description=(f"이번 주 식단 인증 목표는 **{days}회**입니다.\n"
                         "필요에 따라 언제든 목표를 수정할 수 있어요. (다시 목표설정 → 식단 목표)"),
            color=discord.Color.teal()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = MainMenuView()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="1", style=discord.ButtonStyle.primary, custom_id="diet_1")
    async def on_diet_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_diet_selection(interaction, 1)
    @discord.ui.button(label="2", style=discord.ButtonStyle.primary, custom_id="diet_2")
    async def on_diet_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_diet_selection(interaction, 2)
    @discord.ui.button(label="3", style=discord.ButtonStyle.primary, custom_id="diet_3")
    async def on_diet_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_diet_selection(interaction, 3)
    @discord.ui.button(label="4", style=discord.ButtonStyle.primary, custom_id="diet_4")
    async def on_diet_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_diet_selection(interaction, 4)
    @discord.ui.button(label="5", style=discord.ButtonStyle.primary, custom_id="diet_5")
    async def on_diet_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_diet_selection(interaction, 5)
    @discord.ui.button(label="6", style=discord.ButtonStyle.primary, custom_id="diet_6")
    async def on_diet_6(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_diet_selection(interaction, 6)
    @discord.ui.button(label="7", style=discord.ButtonStyle.primary, custom_id="diet_7")
    async def on_diet_7(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_diet_selection(interaction, 7)

    @discord.ui.button(label="🔙 뒤로가기", style=discord.ButtonStyle.danger, custom_id="back_to_goal_from_diet")
    async def on_back_to_goal_from_diet(self, interaction: discord.Interaction, button: discord.ui.Button):
        """뒤로가기 → 목표 유형 선택으로 복귀"""
        embed = discord.Embed(
            title="🏆 운동/식단 목표 설정",
            description=(
                "“🏋️ 운동목표 설정” 또는 “🍽️ 식단목표 설정”을 선택하세요.\n\n"
                "`🔙 뒤로가기` 버튼을 누르면 메인 메뉴로 돌아갑니다."
            ),
            color=discord.Color.blue()
        )
        footer = format_footer(interaction.user)
        embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
        embed.set_thumbnail(url=bot.user.avatar.url)

        view = GoalTypeView()
        await interaction.response.edit_message(embed=embed, view=view)


# -----------------------------------------------------------------------------
# 3) !쌤 커맨드 정의
# -----------------------------------------------------------------------------
@bot.command(name="쌤")
async def 쌤(ctx: commands.Context):
    """!쌤 명령어를 입력하면, 트레이너햄찌봇 메인 메뉴 임베드를 띄웁니다."""
    embed = discord.Embed(
        title="🐹 트레이너 햄찌봇",
        description=(
            "안녕하세요, 공주님! 🐹💖\n\n"
            "여러분의 건강한 운동 생활을 위해 아래 버튼을 눌러주세요:\n\n"
            "🎯 **목표설정**  |  📊 **기록확인**  |  💪🏻 **근육랭킹**\n\n"
            "햄찌가 여러분의 운동·식단을 귀엽게 관리해 드릴게요🌟"
        ),
        color=discord.Color.blurple()
    )
    footer = format_footer(ctx.author)
    embed.set_footer(text=footer["text"], icon_url=footer["icon_url"])
    embed.set_thumbnail(url=bot.user.avatar.url)

    view = MainMenuView()
    await ctx.send(embed=embed, view=view)


# -----------------------------------------------------------------------------
# 4) on_ready 이벤트: 주간/월간 자동 루틴 스케줄링
# -----------------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"✅ {bot.user} 로그인 완료 — {get_kst_now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 1) 매주 일요일 밤 23:00 KST → 주간 DM & 주간 배지 지급
    if not weekly_task.is_running():
        weekly_task.start()

    # 2) 매월 1일 00:10 KST → 월간 트로피 지급
    if not monthly_task.is_running():
        monthly_task.start()


# -----------------------------------------------------------------------------
# 5) 음성 채널 운동 인증: on_voice_state_update
# -----------------------------------------------------------------------------
@bot.event
async def on_voice_state_update(member, before, after):
    user_id = str(member.id)
    # 1) 입장 감지 → 시작 시간 기록
    if after.channel and after.channel.name in TRACKED_VOICE_CHANNELS:
        # 음성 채널에 진입하면 시작 시간 기록
        data = user_goals.setdefault(user_id, {
            "badges": {"weekly_badges": 0, "bikinis": 0, "monthly_trophies": 0},
            "weekly_log": {},
            "voice_session": {}
        })
        data["voice_session"]["start"] = datetime.now(timezone("Asia/Seoul"))

    # 2) 퇴장 감지 → 15분 이상 머물렀으면 운동 1회 기록
    if before.channel and before.channel.name in TRACKED_VOICE_CHANNELS:
        data = user_goals.setdefault(user_id, {
            "badges": {"weekly_badges": 0, "bikinis": 0, "monthly_trophies": 0},
            "weekly_log": {},
            "voice_session": {}
        })
        start_time = data.get("voice_session", {}).get("start")
        if start_time:
            elapsed = (datetime.now(timezone("Asia/Seoul")) - start_time).total_seconds() / 60
            # 15분 이상 머물렀다면
            if elapsed >= 15:
                # 주당 운동 횟수 목표가 설정되어 있으면 증가
                if "frequency_goal" in data:
                    data["frequency_goal"]["achieved_this_week"] = data["frequency_goal"].get("achieved_this_week", 0) + 1
                    # 해당 요일 로그 기록 (월~금만)
                    weekday = datetime.now(timezone("Asia/Seoul")).weekday()  # 0=월,4=금
                    if 0 <= weekday <= 4:
                        day_name = ["월","화","수","목","금"][weekday]
                        data.setdefault("weekly_log", {})[day_name] = True
            # 시작 시간 초기화
            data["voice_session"]["start"] = None


# -----------------------------------------------------------------------------
# 6) 포럼 채널 식단 인증: on_thread_create
# -----------------------------------------------------------------------------
@bot.event
async def on_thread_create(thread: discord.Thread):
    """
    포럼에 새 스레드(포스트)가 생성되면 식단 인증으로 간주하고 카운트.
    """
    channel = thread.parent  # 포럼 채널이 parent 객체
    if channel and channel.type == discord.ChannelType.forum and channel.id == FORUM_CHANNEL_ID:
        user_id = str(thread.owner_id)
        data = user_goals.setdefault(user_id, {
            "badges": {"weekly_badges": 0, "bikinis": 0, "monthly_trophies": 0},
            "weekly_log": {},
            "voice_session": {}
        })
        # 식단 목표가 있다면 증가
        if "diet_goal" in data:
            data["diet_goal"]["achieved_this_week"] = data["diet_goal"].get("achieved_this_week", 0) + 1
            # 해당 요일이 월~금이면 로그 기록
            weekday = datetime.now(timezone("Asia/Seoul")).weekday()
            if 0 <= weekday <= 4:
                day_name = ["월","화","수","목","금"][weekday]
                data.setdefault("weekly_log", {})[day_name] = True


# -----------------------------------------------------------------------------
# 7) DM으로 체중 입력 처리: on_message (DM 채널에서)
# -----------------------------------------------------------------------------
@bot.event
async def on_message(message: discord.Message):
    # 봇 본인 메시지는 무시
    if message.author.bot:
        return

    # DM 채널에서 오는 메시지인가?
    if isinstance(message.channel, discord.DMChannel):
        user_id = str(message.author.id)
        # 1) 초기 체중 감량 목표 설정 단계
        if user_id in weight_dm_context:
            ctx = weight_dm_context[user_id]
            stage = ctx["stage"]
            content = message.content.strip()

            # ---------- 단계 1: “몇 주?” 입력 받기 ----------
            if stage == 1:
                # 숫자(주)만 입력받음
                if content.isdigit() and int(content) > 0:
                    weeks = int(content)
                    weight_dm_context[user_id]["weeks"] = weeks
                    weight_dm_context[user_id]["stage"] = 2
                    await message.channel.send("✅ 좋아요! 목표 기간을 **{}주**로 설정할게요.\n"
                                               "2️⃣ 이제 **현재 체중(kg)**을 알려주세요! (예: 62.5)".format(weeks))
                else:
                    await message.channel.send("❌ 숫자(예: 8)만 입력해주세요. 몇 주 동안 진행할 예정인가요?")

            # ---------- 단계 2: “현재 체중” 입력 받기 ----------
            elif stage == 2:
                try:
                    current_w = float(content)
                    if current_w <= 0:
                        raise ValueError
                    weight_dm_context[user_id]["start_weight"] = current_w
                    weight_dm_context[user_id]["stage"] = 3
                    await message.channel.send("✅ 현재 체중을 **{}kg**으로 기록했어요.\n"
                                               "3️⃣ 마지막으로 **목표 체중(kg)**을 알려주세요! (예: 55.0)".format(current_w))
                except:
                    await message.channel.send("❌ 올바른 체중(예: 62.5) 형태로 입력해주세요.")

            # ---------- 단계 3: “목표 체중” 입력 받기 ----------
            elif stage == 3:
                try:
                    target_w = float(content)
                    if target_w <= 0:
                        raise ValueError
                    ctx["target_weight"] = target_w
                    # 모든 정보 입력 완료 → user_goals에 저장
                    weeks = ctx["weeks"]
                    start_w = ctx["start_weight"]
                    # 진행률 0%, 달성 False
                    user_goals.setdefault(user_id, {
                        "badges": {"weekly_badges": 0, "bikinis": 0, "monthly_trophies": 0},
                        "weekly_log": {},
                        "voice_session": {}
                    })
                    user_goals[user_id]["weight_goal"] = {
                        "weeks": weeks,
                        "start_weight": start_w,
                        "target_weight": target_w,
                        "achieved": False,
                        "progress_pct": 0
                    }
                    # 로그 초기화
                    user_goals[user_id].setdefault("weekly_log", {})
                    for wd in ["월", "화", "수", "목", "금"]:
                        user_goals[user_id]["weekly_log"][wd] = False

                    await message.channel.send(
                        "✅ 체중 감량 목표가 설정되었습니다!\n"
                        f"• 기간: **{weeks}주**\n"
                        f"• 시작 체중: **{start_w}kg**\n"
                        f"• 목표 체중: **{target_w}kg**\n\n"
                        "이제 매주 일요일 밤에 DM으로 현재 체중을 물어볼게요!\n"
                        "“진행률”만 관리되니, 실제 체중 숫자는 서버에 저장되지 않아요. 안심하세요! 😊"
                    )
                    # 컨텍스트 삭제
                    del weight_dm_context[user_id]

                except:
                    await message.channel.send("❌ 올바른 체중(예: 55.0) 형태로 입력해주세요.")

        # ---------- 2) 주간 DM으로 체중 묻는 플로우 (매주) ----------
        # 사용자에게 “이번 주 체중을 입력해 주세요”로 묻고 응답 받기
        elif user_id in weekly_dm_context:
            ctx = weekly_dm_context[user_id]
            # stage 1: 이번 주 체중 입력
            try:
                new_w = float(message.content.strip())
                start_w = user_goals[user_id]["weight_goal"]["start_weight"]
                target_w = user_goals[user_id]["weight_goal"]["target_weight"]
                # 진행률 계산
                total_diff = start_w - target_w
                if total_diff <= 0:
                    pct = 100
                else:
                    progress = start_w - new_w
                    pct = int((progress / total_diff) * 100)
                    if pct < 0:
                        pct = 0
                    if pct > 100:
                        pct = 100

                user_goals[user_id]["weight_goal"]["progress_pct"] = pct
                # 목표 달성 여부
                if new_w <= target_w:
                    user_goals[user_id]["weight_goal"]["achieved"] = True
                    # 비키니 배지 1개 추가 (중복 방지: 이미 획득하지 않았다면)
                    if not user_goals[user_id]["weight_goal"].get("achieved_before", False):
                        user_goals[user_id]["weight_goal"]["achieved_before"] = True
                        user_goals[user_id]["badges"]["bikinis"] += 1

                await message.channel.send(
                    f"✅ 이번 주 체중을 기록했어요! 진행률: **{pct}%**입니다.\n"
                    "주간 목표 체크 결과는 ‘기록확인’에서 확인해주세요! 🐹"
                )

            except:
                await message.channel.send("❌ 숫자(예: 60.3)만 입력해주세요. 다시 “이번 주 체중”을 입력해주세요.")

            # 한 주 DM 완료 → 컨텍스트 삭제
            del weekly_dm_context[user_id]

        # DM 메시지 처리 끝 (서버 채팅 메시지는 아래로 계속)
        return

    # 서버(길드) 채팅에서 발생한 메시지는 반드시 처리하도록
    await bot.process_commands(message)


# -----------------------------------------------------------------------------
# 8) 매주 일요일 밤 23:00 KST → 주간 DM으로 체중 묻고, 주간 목표 달성 시 ‘주간 배지’ 지급
# -----------------------------------------------------------------------------
weekly_dm_context: dict[str, dict] = {}  # {user_id: {"asked": True}}

@tasks.loop(time=time(hour=23, minute=0, tzinfo=timezone("Asia/Seoul")))
async def weekly_task():
    """
    매주 일요일 밤 23:00 KST에 실행됩니다.
    1) 모든 사용자에게 DM으로 “이번 주 체중을 입력해주세요” 요청
    2) 주간 운동 + 식단 달성 여부를 체크하여 ‘주간 배지(weekly_badges)’를 지급
    3) 주간 로그 초기화(다음 주를 위해) → 주간 운동/식단/로그 Reset
    """
    # 1) 체중 DM 전송
    for uid, data in user_goals.items():
        if "weight_goal" in data and not data["weight_goal"].get("achieved", False):
            # 아직 체중 목표를 달성하지 않은 사용자에게만 DM
            try:
                user = await bot.fetch_user(int(uid))
                dm = await user.create_dm()
                await dm.send(
                    "⚖️ **이번 주 체중**을 입력해주세요! (숫자만, 예: 60.5)\n"
                    "__(입력하신 체중은 저장되지 않으며, 진행률만 업데이트됩니다.)_"
                )
                # 응답 받기 위해 컨텍스트 설정
                weekly_dm_context[uid] = {"asked": True}
            except:
                # DM이 불가능하거나 오류 시 무시
                continue

    # 2) 주간 목표 달성 여부 확인 → 주간 배지 지급
    # 주간 배지 조건: “frequency_goal”과 “diet_goal” 모두 달성한 경우 (achieved_this_week >= per_week)
    for uid, data in user_goals.items():
        fg = data.get("frequency_goal", {})
        dg = data.get("diet_goal", {})
        if fg and dg:
            if fg.get("achieved_this_week", 0) >= fg.get("per_week", 0) and \
               dg.get("achieved_this_week", 0) >= dg.get("per_week", 0):
                # 이미 주간 배지 받은 적이 없는 상태로 가정 (매주 발급됨)
                data["badges"]["weekly_badges"] = data["badges"].get("weekly_badges", 0) + 1

    # 3) 주간 기록 초기화 (다음 주를 위해)
    for uid, data in user_goals.items():
        # 운동/식단 달성 횟수 리셋
        if "frequency_goal" in data:
            data["frequency_goal"]["achieved_this_week"] = 0
        if "diet_goal" in data:
            data["diet_goal"]["achieved_this_week"] = 0
        # 주간 로그(월~금) 리셋
        if "weekly_log" in data:
            for wd in ["월","화","수","목","금"]:
                data["weekly_log"][wd] = False

    # 다음주를 위해 task가 다시 대기
    # (tasks.loop는 자동으로 다음 스케줄을 기다립니다)


# -----------------------------------------------------------------------------
# 9) 매월 1일 00:10 KST → 월간 트로피 지급 (한 달 동안 매주 배지를 얻은 사람에게)
# -----------------------------------------------------------------------------
@tasks.loop(time=time(hour=0, minute=10, tzinfo=timezone("Asia/Seoul")))
async def monthly_task():
    """
    매월 1일 00:10 KST에 실행됩니다.
    한 달(즉 지난 달)에 얻은 주간 배지 개수가 **>= 4**(약 4주)라면 ‘월간 트로피’ 지급
    지급 후, 해당 사용자의 주간 배지 수는 0으로 초기화합니다.
    """
    for uid, data in user_goals.items():
        weekly_badges = data["badges"].get("weekly_badges", 0)
        if weekly_badges >= 4:
            data["badges"]["monthly_trophies"] = data["badges"].get("monthly_trophies", 0) + 1
        # 한 달 단위 체크였으므로 주간 배지 수 초기화
        data["badges"]["weekly_badges"] = 0


# -----------------------------------------------------------------------------
# 10) DM으로 체중 목표 설정 플로우
#     → on_message(DM)에서 처리하도록 되어 있음 (위에서 구현됨)
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# 11) 봇 준비 완료 시 작업 스케줄링
# -----------------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"✅ {bot.user} 로그인 완료 — {get_kst_now().strftime('%Y-%m-%d %H:%M:%S')}")
    # 중복 실행 방지
    if not weekly_task.is_running():
        weekly_task.start()
    if not monthly_task.is_running():
        monthly_task.start()


# -----------------------------------------------------------------------------
# 12) 봇 실행
# -----------------------------------------------------------------------------
bot.run(TOKEN)
