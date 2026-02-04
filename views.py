import discord
from discord import ui
from typing import Callable, Awaitable
import config


class RatingSelect(ui.Select):
    """Выпадающее меню для выбора рейтинга"""
    
    def __init__(
        self, 
        placeholder: str, 
        custom_id: str,
        callback_func: Callable[..., Awaitable]
    ):
        options = [
            discord.SelectOption(
                label=f"BR {rating:.1f}",
                value=str(rating),
                description=f"Боевой рейтинг {rating:.1f}"
            )
            for rating in config.RATINGS
        ]
        super().__init__(
            placeholder=placeholder,
            options=options,
            custom_id=custom_id
        )
        self.callback_func = callback_func
    
    async def callback(self, interaction: discord.Interaction):
        await self.callback_func(interaction, float(self.values[0]))


class FindMateView(ui.View):
    """Основной View для выбора рейтингов"""
    
    def __init__(self, bot):
        super().__init__(timeout=300)  # 5 минут на выбор
        self.bot = bot
        self.user_rating: float = None
        self.desired_rating: float = None
        
        self.user_rating_select = RatingSelect(
            placeholder="🎮 Выберите ВАШ боевой рейтинг",
            custom_id="user_rating",
            callback_func=self.on_user_rating_select
        )
        self.add_item(self.user_rating_select)
        
        self.desired_rating_select = RatingSelect(
            placeholder="🎯 Выберите ЖЕЛАЕМЫЙ рейтинг напарника",
            custom_id="desired_rating",
            callback_func=self.on_desired_rating_select
        )
        self.desired_rating_select.disabled = True
        self.add_item(self.desired_rating_select)
    
    async def on_user_rating_select(
        self, 
        interaction: discord.Interaction, 
        rating: float
    ):
        """Обработка выбора своего рейтинга"""
        self.user_rating = rating
        self.desired_rating_select.disabled = False
        
        embed = discord.Embed(
            title="🔍 Поиск напарника",
            description=f"✅ Ваш рейтинг: **BR {rating:.1f}**\n\n"
                       f"Теперь выберите желаемый рейтинг напарника.",
            color=discord.Color.blue()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def on_desired_rating_select(
        self, 
        interaction: discord.Interaction, 
        rating: float
    ):
        """Обработка выбора желаемого рейтинга напарника"""
        self.desired_rating = rating
        
        for item in self.children:
            item.disabled = True
        
        embed = discord.Embed(
            title="⏳ Поиск напарника...",
            description=f"**Ваш рейтинг:** BR {self.user_rating:.1f}\n"
                       f"**Ищем напарника с рейтингом:** BR {self.desired_rating:.1f}\n\n"
                       f"Ожидайте, мы найдём вам напарника!",
            color=discord.Color.gold()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        await self.bot.process_matchmaking(
            interaction, 
            self.user_rating, 
            self.desired_rating
        )
    
    async def on_timeout(self):
        """Обработка таймаута"""
        for item in self.children:
            item.disabled = True


class CancelSearchView(ui.View):
    """View с кнопкой отмены поиска"""
    
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
    
    @ui.button(
        label="❌ Отменить поиск", 
        style=discord.ButtonStyle.danger,
        custom_id="cancel_search"
    )
    async def cancel_button(
        self, 
        interaction: discord.Interaction, 
        button: ui.Button
    ):
        await self.bot.cancel_search(interaction)


class CloseChannelView(ui.View):
    """View с кнопкой закрытия канала"""
    
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
    
    @ui.button(
        label="🗑️ Закрыть канал", 
        style=discord.ButtonStyle.secondary,
        custom_id="close_channel"
    )
    async def close_button(
        self, 
        interaction: discord.Interaction, 
        button: ui.Button
    ):
        await self.bot.close_match_channel(interaction)