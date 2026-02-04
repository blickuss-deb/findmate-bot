import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
from datetime import datetime

import config
from database import db
from views import FindMateView, CancelSearchView, CloseChannelView


class FindMateBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
    
    async def setup_hook(self):
        """Инициализация при запуске бота"""
        await db.init()
        
        self.add_view(CancelSearchView(self))
        self.add_view(CloseChannelView(self))
        
        await self.tree.sync()
        
        self.cleanup_task.start()
        
        print(f"Бот готов к работе!")
    
    async def on_ready(self):
        print(f"🤖 Logged in as {self.user} (ID: {self.user.id})")
        print(f"📊 Connected to {len(self.guilds)} guilds")
        
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name="/findmate | Поиск союзников"
        )
        await self.change_presence(activity=activity)
    
    @tasks.loop(minutes=5)
    async def cleanup_task(self):
        """Фоновая задача для очистки"""
        try:
            inactive_channels = await db.get_inactive_channels(
                config.CHANNEL_INACTIVE_TIMEOUT
            )
            
            for channel_id in inactive_channels:
                try:
                    channel = self.get_channel(channel_id)
                    if channel:
                        await channel.delete(reason="Канал неактивен")
                    await db.remove_channel(channel_id)
                except Exception as e:
                    print(f"Ошибка удаления канала {channel_id}: {e}")
            
            expired_requests = await db.get_expired_requests(
                config.REQUEST_TIMEOUT
            )
            
            for request in expired_requests:
                try:
                    if request['channel_id']:
                        channel = self.get_channel(request['channel_id'])
                        if channel:
                            await channel.delete(reason="Время поиска истекло")
                        await db.remove_channel(request['channel_id'])
                    
                    await db.expire_request(request['id'])
                    
                    try:
                        user = await self.fetch_user(request['user_id'])
                        embed = discord.Embed(
                            title="⏰ Время поиска истекло",
                            description="Ваша заявка на поиск напарника была автоматически "
                                       "отменена из-за истечения времени ожидания.\n\n"
                                       "Используйте `/findmate` для нового поиска.",
                            color=discord.Color.orange()
                        )
                        await user.send(embed=embed)
                    except:
                        pass
                        
                except Exception as e:
                    print(f"Ошибка обработки заявки {request['id']}: {e}")
                    
        except Exception as e:
            print(f"Ошибка в cleanup_task: {e}")
    
    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.wait_until_ready()
    
    async def process_matchmaking(
        self, 
        interaction: discord.Interaction,
        user_rating: float,
        desired_rating: float
    ):
        """Обработка поиска напарника"""
        user = interaction.user
        guild = interaction.guild
        
        match = await db.find_match(
            user.id, 
            guild.id, 
            user_rating, 
            desired_rating
        )
        
        if match:
            await self._complete_match(interaction, match, user_rating, desired_rating)
        else:
            await self._create_waiting_request(
                interaction, 
                user_rating, 
                desired_rating
            )
    
    async def _create_waiting_request(
        self,
        interaction: discord.Interaction,
        user_rating: float,
        desired_rating: float
    ):
        """Создание заявки и канала ожидания"""
        user = interaction.user
        guild = interaction.guild
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_channels=True
            )
        }
        
        category = None
        if config.MATCHMAKING_CATEGORY_ID:
            category = guild.get_channel(config.MATCHMAKING_CATEGORY_ID)
        
        channel_name = f"🔍поиск-{user.name[:15]}"
        
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=category,
                reason=f"Поиск напарника для {user}"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Не удалось создать канал. Проверьте права бота.",
                ephemeral=True
            )
            return
        
        request_id = await db.create_request(
            user.id,
            guild.id,
            user_rating,
            desired_rating,
            channel.id
        )
        
        await db.add_private_channel(
            channel.id,
            guild.id,
            user.id,
            request_id
        )
        
        embed = discord.Embed(
            title="⏳ Ожидание напарника",
            description=f"**Ваш рейтинг:** BR {user_rating:.1f}\n"
                       f"**Ищете напарника с рейтингом:** BR {desired_rating:.1f}\n\n"
                       f"Ожидайте, пока найдётся подходящий игрок...\n"
                       f"Вы можете отменить поиск кнопкой ниже.",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Заявка #{request_id}")
        
        await channel.send(
            content=user.mention,
            embed=embed,
            view=CancelSearchView(self)
        )
        
        await interaction.followup.send(
            f"✅ Заявка создана! Перейдите в канал {channel.mention}",
            ephemeral=True
        )
    
    async def _complete_match(
        self,
        interaction: discord.Interaction,
        match: dict,
        user_rating: float,
        desired_rating: float
    ):
        """Соединение двух игроков"""
        user = interaction.user
        guild = interaction.guild
        
        channel = guild.get_channel(match['channel_id'])
        
        if not channel:
            await self._create_waiting_request(
                interaction, 
                user_rating, 
                desired_rating
            )
            return
        
        partner = guild.get_member(match['user_id'])
        if not partner:
            try:
                partner = await guild.fetch_member(match['user_id'])
            except:
                await db.cancel_request(match['user_id'], guild.id)
                await self._create_waiting_request(
                    interaction, 
                    user_rating, 
                    desired_rating
                )
                return
        
        await channel.set_permissions(
            user,
            read_messages=True,
            send_messages=True,
            read_message_history=True
        )
        
        await db.complete_match(match['id'], user.id)
        await db.update_channel_partner(channel.id, user.id)
        
        try:
            await channel.edit(name=f"🎮match-{partner.name[:8]}-{user.name[:8]}")
        except:
            pass
        
        embed = discord.Embed(
            title="🎉 Напарник найден!",
            description=f"**Игрок 1:** {partner.mention} (BR {match['user_rating']:.1f})\n"
                       f"**Игрок 2:** {user.mention} (BR {user_rating:.1f})\n\n"
                       f"Удачной игры! 🎮",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.set_footer(text="Канал можно закрыть кнопкой ниже")
        
        await channel.send(
            content=f"{partner.mention} {user.mention}",
            embed=embed,
            view=CloseChannelView(self)
        )
        
        await interaction.followup.send(
            f"🎉 Напарник найден! Перейдите в канал {channel.mention}",
            ephemeral=True
        )
    
    async def cancel_search(self, interaction: discord.Interaction):
        """Отмена поиска"""
        user = interaction.user
        guild = interaction.guild
        
        channel_id = await db.cancel_request(user.id, guild.id)
        
        if channel_id:
            embed = discord.Embed(
                title="❌ Поиск отменён",
                description="Ваша заявка на поиск напарника отменена.\n"
                           "Канал будет удалён через 10 секунд.",
                color=discord.Color.red()
            )
            
            await interaction.response.send_message(embed=embed)
            
            await asyncio.sleep(10)
            
            try:
                channel = guild.get_channel(channel_id)
                if channel:
                    await channel.delete(reason="Поиск отменён пользователем")
                await db.remove_channel(channel_id)
            except:
                pass
        else:
            await interaction.response.send_message(
                "У вас нет активных заявок на поиск.",
                ephemeral=True
            )
    
    async def close_match_channel(self, interaction: discord.Interaction):
        """Закрытие канала матча"""
        channel = interaction.channel
        
        embed = discord.Embed(
            title="👋 Канал закрывается",
            description="Канал будет удалён через 10 секунд.\n"
                       "Спасибо за использование бота!",
            color=discord.Color.blue()
        )
        
        await interaction.response.send_message(embed=embed)
        
        await asyncio.sleep(10)
        
        try:
            await db.remove_channel(channel.id)
            await channel.delete(reason="Закрыто пользователем")
        except:
            pass


bot = FindMateBot()


# Команды
@bot.tree.command(
    name="findmate",
    description="🔍 Найди напарника для игры"
)
async def findmate(interaction: discord.Interaction):
    """Команда поиска напарника"""
    
    active_count = await db.get_active_requests_count(
        interaction.user.id, 
        interaction.guild.id
    )
    
    if active_count >= config.MAX_ACTIVE_REQUESTS_PER_USER:
        request = await db.get_user_request(
            interaction.user.id, 
            interaction.guild.id
        )
        
        channel_mention = ""
        if request and request['channel_id']:
            channel = interaction.guild.get_channel(request['channel_id'])
            if channel:
                channel_mention = f"\n\nВаш канал ожидания: {channel.mention}"
        
        embed = discord.Embed(
            title="⚠️ Лимит заявок",
            description=f"У вас уже есть активная заявка на поиск напарника.\n"
                       f"Отмените её перед созданием новой.{channel_mention}",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🔍 Поиск напарника",
        description="Выберите **ваш** боевой рейтинг, затем выберите "
                   "**желаемый** рейтинг напарника.\n\n"
                   "⏱️ У вас есть 5 минут на выбор.",
        color=discord.Color.blue()
    )
    
    view = FindMateView(bot)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(
    name="cancelmate",
    description="❌ Отменить поиск напарника"
)
async def cancelmate(interaction: discord.Interaction):
    """Команда отмены поиска"""
    
    request = await db.get_user_request(
        interaction.user.id, 
        interaction.guild.id
    )
    
    if not request:
        await interaction.response.send_message(
            "У вас нет активных заявок на поиск.",
            ephemeral=True
        )
        return
    
    channel_id = await db.cancel_request(
        interaction.user.id, 
        interaction.guild.id
    )
    
    embed = discord.Embed(
        title="✅ Поиск отменён",
        description="Ваша заявка на поиск напарника успешно отменена.",
        color=discord.Color.green()
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
    if channel_id:
        try:
            channel = interaction.guild.get_channel(channel_id)
            if channel:
                await channel.delete(reason="Поиск отменён")
            await db.remove_channel(channel_id)
        except:
            pass


@bot.tree.command(
    name="matestatus",
    description="📊 Статус вашей заявки на поиск"
)
async def matestatus(interaction: discord.Interaction):
    """Команда проверки статуса"""
    
    request = await db.get_user_request(
        interaction.user.id, 
        interaction.guild.id
    )
    
    if not request:
        embed = discord.Embed(
            title="📊 Статус поиска",
            description="У вас нет активных заявок.\n"
                       "Используйте `/findmate` для начала поиска.",
            color=discord.Color.blue()
        )
    else:
        channel = interaction.guild.get_channel(request['channel_id'])
        channel_mention = channel.mention if channel else "Канал не найден"
        
        embed = discord.Embed(
            title="📊 Статус поиска",
            description=f"**Ваш рейтинг:** BR {request['user_rating']:.1f}\n"
                       f"**Ищете:** BR {request['desired_rating']:.1f}\n"
                       f"**Канал:** {channel_mention}\n"
                       f"**Статус:** ⏳ Ожидание напарника",
            color=discord.Color.gold()
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    if message.channel:
        await db.update_channel_activity(message.channel.id)
    
    await bot.process_commands(message)

if __name__ == "__main__":
    if not config.BOT_TOKEN:
        print("❌ Ошибка: Не указан токен бота!")
        print("Создайте файл .env и добавьте DISCORD_BOT_TOKEN=ваш_токен")
        exit(1)
    
    bot.run(config.BOT_TOKEN)