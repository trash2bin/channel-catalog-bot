"""
Лента рекомендаций и ее создание выдача пользователю, ее прокрутка
"""

import string
import random
from telebot.async_telebot import AsyncTeleBot
from telebot import types

from django.core.cache import cache
from django.db.models import Q, F
from django.contrib.gis.measure import D

from mainBot.midleware.cache_tools import set_user_state
from mainBot.midleware.text_tools import get_message_text, anketa_text
from mainBot.telegram.keyboards import *
from mainBot.telegram.handlers.base_handlers import stop_action
from mainBot.models import * # импорт всех моделей Django


# Закодирование id канала чтобы нельзя было подсмотреть через кнопку 
BASE62_ALPHABET = string.digits + string.ascii_letters

async def encode_base62(number):
    """Кодирование числа в Base62."""
    if number == 0:
        return BASE62_ALPHABET[0]
    
    is_negative = number < 0
    number = abs(number)
    base62 = []
    
    while number:
        number, rem = divmod(number, 62)
        base62.append(BASE62_ALPHABET[rem])
    
    encoded = ''.join(reversed(base62))
    return f"-{encoded}" if is_negative else encoded

async def decode_base62(base62):
    """Декодирование Base62 в число."""
    is_negative = base62[0] == "-"
    if is_negative:
        base62 = base62[1:]
    
    number = 0
    for char in base62:
        number = number * 62 + BASE62_ALPHABET.index(char)
    
    return -number if is_negative else number


#! Генерация ленты рекомендаций
async def generate_recommendations(user: User) -> list: 
    #try:
    # Извлечение истории просмотра из Django Cache
    viewed_channels = await cache.aget(f"user:{user.external_id}:viewed", [])
    channels = Channel.objects.filter(is_work=True).exclude(external_id__in=viewed_channels)

    # Учет геолокации
    if user.region and user.location:
        channels = channels.filter(
            Q(region=user.region) | Q(location__distance_lte=(user.location, D(km=50)))
        )
    elif user.region:
        channels = channels.filter(region=user.region)
    else:
        channels = channels.all()

    # Учет категорий пользователя
    user_categories = user.categories.all()
    if await user_categories.aexists():
        interest_based = channels.filter(categories__in=user_categories).distinct()
    else:
        interest_based = Channel.objects.none()

    # Выборка каналов по интересам (70%)
    interest_based_count = int(0.7 * 10)  # 70% от 10 рекомендаций
    interest_based = [
        ch async for ch in interest_based.order_by("-add_time")[:interest_based_count]
    ]

    # Выборка случайных каналов (30%)
    random_count = 10 - len(interest_based)
    all_channels = [ch async for ch in channels.exclude(id__in=[ch.id for ch in interest_based]).all()]
    random_channels = random.sample(all_channels, min(random_count, len(all_channels)))

    # Объединение списков
    combined_recommendations = interest_based + random_channels
    random.shuffle(combined_recommendations)  # Перемешиваем для разнообразия

    return combined_recommendations
    # except Exception as e:
    #     print(f"Ошибка при генерации рекомендаций: {e}")
    #     return []

# Реакция на кнопку ждя создания ленты
async def callback_feed_start(call: types.CallbackQuery, bot: AsyncTeleBot, user: User = None):
    """call.data == 'callback_feed_start'"""
    await recommendations_feed(call.message, bot, call.from_user.id, user)

async def recommendations_feed(message: types.Message, bot: AsyncTeleBot, user_id, user: User = None):
    """
    Функция по построению ленты и выдачи постов
    """
    # Убираем клавиатуру чтобы больше не нажимал
    # await bot.edit_message_reply_markup(
    #     message.chat.id,
    #     message.id,
    #     reply_markup=None
    # )

    recommendations = await cache.aget(f'{user_id}-recommendations')
    # -Рекомендации-
    if not recommendations or len(recommendations) == 0:
        if not user or user == True:
            recommendations = await generate_recommendations(await User.objects.aget(external_id=user_id))
        else:
            recommendations = await generate_recommendations(user)
        # Сохраняем в кэш список рекомендаций     
        await cache.aset(f'{user_id}-recommendations', recommendations, 60*60)

    if not recommendations:
        await bot.send_message(message.chat.id, await get_message_text("general", "feed_end"))
        return

    # Берем канал для отправки
    channel = recommendations[0]
    # Удаялем
    recommendations.pop(0) 
    await cache.aset(f'{user_id}-recommendations', recommendations, 60*60)

    viewed = await cache.aget(f"user:{user_id}:viewed", [])
    if channel.external_id not in viewed:
        await cache.aset(f"user:{user_id}:viewed", (viewed + [channel.external_id])[-200:], 60*60*24*3)
    
    caption = anketa_text(
        channel.title,
        channel.description,
        channel.folowers,
        channel.region,
        channel.likes,
        channel.dislikes
    )
    hash_id = await encode_base62(channel.external_id)
    #! Отправить несколько фото
    imges = channel.poster.split()
    if len(imges) > 1:
        await cache.aset(f'{hash_id}-imgs', imges, 5*60)
    else:
        await cache.aset(f'{hash_id}-imgs', False, 5*60)

    imges_input = []
    for img in imges:
        imges_input.append(types.InputMediaPhoto(img, caption, parse_mode='HTML'))
        break

    msg = await bot.send_media_group(
        message.chat.id, 
        imges_input
    )

    await bot.edit_message_caption(
        caption,
        message.chat.id,
        msg[0].id,
        reply_markup=(await keyboard_post(
            hash_id, 
            await encode_base62(channel.id)
            )
        ),
        parse_mode='HTML'
    )

    # await bot.send_message(
    #     message.chat.id,
    #     caption,
    #     reply_markup=(await keyboard_post(await encode_base62(channel.external_id), 
    #                                       await encode_base62(channel.id))),
    #     parse_mode='HTML'
    # )

#! Изменить фото из имеющихся
async def swap_imgs(call: types.CallbackQuery, bot: AsyncTeleBot): 
    """ callback_data=f'imgs:{i}:{hash}:{hash_id_channel}' """

    hash = call.data.split(':')[2]
    id_imgs = await cache.aget(f'{hash}-imgs')
    hash_id_channel = call.data.split(':')[3]
    if not id_imgs:
        external_id = await decode_base62(hash) 
        id_imgs = ((await Channel.objects.aget(external_id=external_id)).poster).split()
        if len(id_imgs) >= 1:
            await cache.aset(f'{hash}', id_imgs, 5*60)
     
    i = call.data.split(':')[1]   
    id_img = id_imgs[int(i)]

    if id_img != call.message.photo[0].file_id:
        # Создаем объект нового медиа с фото
        new_media = types.InputMediaPhoto(media=id_img, caption=call.message.caption)
        # Редактируем медиа в сообщении
        await bot.edit_message_media(
            chat_id=call.message.chat.id,
            message_id=call.message.id, 
            media=new_media,
            reply_markup= await keyboard_post(hash, hash_id_channel, int(i))
        )

# Dislike:
async def callback_dislike(call: types.CallbackQuery, bot: AsyncTeleBot, user: User = None):
    """ call.data.startswith("dislike_post-") """
    # Убираем клавиатуру чтобы больше не нажимал
    await bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup=None
    )    

    hash_code = call.data.split(":")[1]
    channel_id = await decode_base62(hash_code)

    # Обновляем
    await Channel.objects.filter(external_id=channel_id).aupdate(dislikes=F("dislikes") + 1)

    await recommendations_feed(call.message, bot, call.from_user.id, user)

# Like:
async def callback_like(call: types.CallbackQuery, bot: AsyncTeleBot, user: User = None):
    """ call.data.startswith("like_post-") """

    code = call.data.split(":")[1]
    hash_code = call.data.split(":")[2]
    channel_id = await decode_base62(code)

    # Обновляем
    await Channel.objects.filter(external_id=channel_id).aupdate(likes=F("likes") + 1)

    keyboard = types.InlineKeyboardMarkup().add(
        InlineKeyboardButton("Ссылка", url=f't.me/{(await bot.get_chat(channel_id)).username}'),
        # Жалоба
        InlineKeyboardButton("⚠️", callback_data=f'complaint_post:{code}:{hash_code}')
    )
    # Убираем клавиатуру чтобы больше не нажимал
    await bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup=keyboard
    )    

    await recommendations_feed(call.message, bot, call.from_user.id, user)

# Отвечаем на коммент
async def comment_status(call: types.CallbackQuery, bot: AsyncTeleBot):
    """ call.data.starstwith('comment_post+') """

    msg = await bot.send_message(
        call.message.chat.id,
        await get_message_text('general', 'comment_status'),
        reply_markup=await stop_message(),
        parse_mode='HTML'
    )
    
    hash_code = call.data.split(":")[2]
    code = call.data.split(":")[1]

    # Ставим в кэш наш код канала
    await cache.aset(f'{call.from_user.id}-comment-tg', hash_code, 30*60)
    await cache.aset(f'{call.from_user.id}-id_botmessage', [msg.id], 30*60)
    await set_user_state(call.from_user.id, 'comment')

    keyboard = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton(
            "Ссылка", 
            url=f't.me/{(await bot.get_chat(await decode_base62(call.data.split(":")[1]))).username}'
            ),
        InlineKeyboardButton("⚠️", callback_data=f'complaint_post:{code}:{hash_code}')
    )
    # Убираем клавиатуру чтобы больше не нажимал
    await bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup=keyboard
    )       

async def comment_send(message: types.Message, bot: AsyncTeleBot, user: User = None):
    """ status == 'comment' """
    # Если пользователь хочет выйти
    if await stop_action(message, bot):
        await bot.delete_messages(
            message.chat.id, 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [message.id]
            ) 
        cache.delete(f'{message.from_user.id}-id_botmessage')
        cache.delete(f'{message.from_user.id}-comment-tg')
        await set_user_state(message.from_user.id, None)
 
        return True
    
    hash_code = await cache.aget(f'{message.from_user.id}-comment-tg')
    if not hash_code: # Пользователь чебурек
        await bot.send_message(
            message.chat.id, 
            await get_message_text('errors', 'not_found_cache'), 
            parse_mode='HTML'
            )
        await set_user_state(message.from_user.id, None)
        return True   

    # Проверка длины текста
    if len(message.text) > 512:
        msg = await bot.send_message(
            message.chat.id, 
            await get_message_text('errors', 'limit_simvols'), 
            parse_mode='HTML'
        )      
        await cache.aset(
            f'{message.from_user.id}-id_botmessage', 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [msg.id]
        )        
        return True
        
    if not user or user == True: # Запросы к бд
        user = await User.objects.aget(external_id=message.from_user.id)
    await Comment.objects.acreate(
        user_id=user.id,
        channel_id=await decode_base62(hash_code),
        text=message.text,
        is_viewed=False
    )          
    await bot.send_message(
            message.chat.id, 
            await get_message_text('general', 'coment_complite'), 
            reply_to_message_id=message.id,
            parse_mode='HTML'
        )  
    await set_user_state(message.from_user.id, None)
    # Мотаем ленту дальше
    await recommendations_feed(message, bot, message.from_user.id, user)    

# Выложить список жалоб
async def complite_category_collback(call: types.CallbackQuery, bot: AsyncTeleBot):
    """ Список все категорий для жалобы """

    await bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup=await complite_tags_keybord(call.data.split(":")[1], call.data.split(":")[2])
        )    

# Назад в оценку
async def feed_back_collback(call: types.CallbackQuery, bot: AsyncTeleBot):
    """ back """
    await bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup=await keyboard_post(call.data.split(":")[1], call.data.split(":")[2])
        )
# Реакция на выбранную категорию для жалобы    
async def complite_category_choice(call: types.CallbackQuery, bot: AsyncTeleBot):
    """
    call.data == 'complite_tags:{cp.id}:{hash}:{hash_id_channel}' 
    """
    item_id = call.data.split(":")[1]
    hash = call.data.split(":")[2]
    hash_id_channel = call.data.split(":")[3]

    await bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup= await complite_tags_keybord_finish(item_id, hash, hash_id_channel)
        )        
    
#! Финальное добавление жалобы в бд    
async def complite_category_complite(call: types.CallbackQuery, bot: AsyncTeleBot, user: User=None): 
    """
    call.data = f'tags_complite:{item_id}:{hash}:{hash_id_channel}'
    """   
    await bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup= None
        )     

    hash_code = call.data.split(':')[2]
    item_id = call.data.split(':')[1]
    hash_id_channel = call.data.split(":")[3]

    if not user or user == True: # Запросы к бд
        user = await User.objects.aget(external_id=call.from_user.id)
    await Complaint.objects.acreate(
        user_id=user.id,
        channel_id=await decode_base62(hash_id_channel),
        category_id=item_id,
        is_viewed=False
    ) 

    await bot.answer_callback_query(  # Отчет
        call.id,
        await get_message_text('general', 'coment_complite')
        )
    
    await recommendations_feed(call.message, bot, call.from_user.id, user) 
