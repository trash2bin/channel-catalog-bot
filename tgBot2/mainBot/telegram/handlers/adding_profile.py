# Основные функции бота, пряма разработка функционала
import aiohttp
import io
import re
from PIL import Image, ImageDraw
from telebot.async_telebot import AsyncTeleBot
from telebot import types
from django.core.cache import cache, caches
from django.contrib.gis.geos import Point

from mainBot.midleware.cache_tools import set_user_state, category_cache
from mainBot.midleware.text_tools import get_message_text, anketa_text, ban_words_cheking, extract_text, extract_link

from mainBot.telegram.keyboards import *
from mainBot.telegram.geo_utils import geocode
from mainBot.telegram.handlers.msg_to_chat import callback_change_channel_categories
from mainBot.telegram.handlers.base_handlers import stop_action
from mainBot.models import * # импорт всех моделей Django


#! Процесс создания анкеты:
    #* Ответ на inlane кнопку под сообщением /start
async def callback_add_channel_start(call: types.CallbackQuery, bot: AsyncTeleBot): #*  call.data = add_channel_start
    await bot.answer_callback_query(call.id, await get_message_text('callback_query', 'rules'))
    # Клавиатура для выбора способа добавления канала
    keyboard = types.InlineKeyboardMarkup(row_width=1).add(
        types.InlineKeyboardButton(text= await get_message_text('keyboards', 'add_channel_bio'), 
                                   callback_data=f"add_channel_bio"),
        types.InlineKeyboardButton(text= await get_message_text('keyboards', 'add_channel_parsing'), 
                                   callback_data=f"add_channel_parsing"),
    )
    # Меняем текст основного сообщения на то как добавить канал
    await bot.edit_message_text(
        chat_id = call.message.chat.id,
        message_id = call.message.message_id, 
        text = await get_message_text('general', 'channel_adding_information'), 
        reply_markup = keyboard,
        parse_mode='HTML'
        )
    
#! Вариант через добавления личного канала из своего телеграм профиля:
async def callback_add_channel_bio(call: types.CallbackQuery, bot: AsyncTeleBot): 
    """ call.data = add_channel_bio """

    profile = await bot.get_chat(call.from_user.id)
    if profile.personal_chat:
        channel = await bot.get_chat(profile.personal_chat.id)
        # Проверяем вдруг канал уже добавил кто то 
        error_add_channel = await check_channel(channel.id)
        if error_add_channel:       
            await bot.send_message(
                chat_id=call.message.chat.id,
                text=error_add_channel, # Показываем кто добавил этот канал
                parse_mode="HTML",
            )            
            return True # чтобы ошибок не было   
        # Добавлем в кэш тгк
        await cache.aset(f'{call.from_user.id}-channel', channel, settings.CACHE_CREATE)
        await cache.aset(f'{call.from_user.id}-descriptionChannal', channel.description, settings.CACHE_CREATE)

        folowers = await bot.get_chat_member_count(channel.id)       
        await cache.aset(f'{call.from_user.id}-folowers', folowers, settings.CACHE_CREATE)

        text = anketa_text(
            channel.title, 
            channel.description, 
            folowers
            ) + f'\nt.me/{channel.username}'
        await download_pic_send(
            channel,
            bot, 
            call.from_user.id,
            call.message,
            text
        )
 
    else:
        # Если тгк в профиле нету
        await bot.answer_callback_query(call.id, await get_message_text('errors', 'not_found'))
        eror =  await get_message_text('errors', 'not_found_chennal_bio')
        # Избегаем ошибки телеграмм апи на отсуствие изменений 
        if not(eror in call.message.text):
            # Показываем ошибку что юзер дурак
            await bot.edit_message_text(
                chat_id = call.message.chat.id,
                message_id = call.message.message_id, 
                text = call.message.text+'\r\n\r\n'+eror, 
                reply_markup = await update_keyboard_warning(call, 'add_channel_bio', 3),
                parse_mode='HTML'
                )

#! Пользователь хочет отправить сам фото:             
async def add_channel_img_chat(call: types.CallbackQuery, bot: AsyncTeleBot): 
    """ call.data = add_channel_img_chat """

    await set_user_state(call.from_user.id, 'add_channel_img_chat')
    msg = await bot.send_message(
        call.message.chat.id, 
        await get_message_text('general', 'add_channel_img_chat'), 
        reply_markup=(await stop_message()),
        parse_mode='HTML'
        )
    # Храним id сообщения тг чтобы работать красиво
    await cache.aset(f'{call.from_user.id}-id_message', call.message.id ,settings.CACHE_CREATE)
    await cache.aset(f'{call.from_user.id}-id_botmessage', [msg.id] ,settings.CACHE_CREATE)

#* Отправка самому фотографии в чат    
async def add_channel_img_chat_chat(message: types.Message, bot: AsyncTeleBot):
    async def replace_line(strings: list, target: str, new_value: str) -> int:
        """ Свап фото из всего списка """
        for i, line in enumerate(strings):
            if line == target:
                strings[i] = new_value
                return i
        strings[0] = new_value # В случаве если ничего не совпало
            
    if message.photo:
        id_imgs = await cache.aget(f'{message.from_user.id}-id_imgs')
        i = None # Проверяем одна ли фотогрфия или несколько записываем сюда какая из нескольких
        if not id_imgs:
            cache.delete(f'{message.from_user.id}-stock_img') 
        elif id_imgs or len(id_imgs) > 1:  # Меняем в памяти новое изображение из всего стака
            stock_img = await cache.aget(f'{message.from_user.id}-stock_img')
            # Меняем во всем списке тоже
            i = await replace_line(id_imgs, stock_img, message.photo[0].file_id)
            # Сохраняем новый лист
            await cache.aset(f'{message.from_user.id}-id_imgs', id_imgs, settings.CACHE_CREATE) 
            cache.delete(f'{message.from_user.id}-stock_img')  

        # Изменияем сообщение в связи с новой информацией об анкете
        channel = await change_message(
            bot,
            message.from_user.id,
            message.chat.id,
            message.id,
            message.photo[0].file_id,
            0 if not i else i
        )
        if not channel:
            return True # Выходим
    else:
        msg = await bot.send_message(
            message.chat.id, 
            await get_message_text('errors', 'not_found_img'), 
            parse_mode='HTML'
        )
        # Создаем лист служеюных сообщений чтобы потом их удалить
        await cache.aset(
            f'{message.from_user.id}-id_botmessage', 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [msg.id], 
            settings.CACHE_CREATE
            )
        await bot.delete_message(message.chat.id, message.id)

#! Пользователь хочет добавить фото:             
async def add_channel_more_img(call: types.CallbackQuery, bot: AsyncTeleBot): 
    """ call.data = add_channel_more_img """

    imgs_id = await cache.aget(f'{call.from_user.id}-id_imgs')
    if imgs_id:
        # Выходим если и так лимит и сообщаем об этом
        if len(imgs_id) > 4:
            await bot.answer_callback_query(
                call.id, 
                await get_message_text('errors', 'limit_imgs'), 
                True
            )           
            return True 
    else:    
        await cache.aset(f'{call.from_user.id}-id_imgs', [call.message.photo[0].file_id] ,settings.CACHE_CREATE)

    await set_user_state(call.from_user.id, 'add_channel_more_img_chat')
    msg = await bot.send_message(
        call.message.chat.id, 
        await get_message_text('general', 'add_channel_more_img_chat'), 
        reply_markup=(await stop_message()),
        parse_mode='HTML'
        )
    # Храним id сообщения тг чтобы работать красиво
    await cache.aset(f'{call.from_user.id}-id_message', call.message.id ,settings.CACHE_CREATE)
    await cache.aset(f'{call.from_user.id}-id_botmessage', [msg.id] ,settings.CACHE_CREATE)
        
#* sending in chat    
async def add_channel_more_img_chat(message: types.Message, bot: AsyncTeleBot):
    async def limit_msg(message: types.Message, bot: AsyncTeleBot):
        msg = await bot.send_message(
            message.chat.id, 
            await get_message_text('errors', 'limit_imgs'), 
            parse_mode='HTML'
        )
        # Создаем лист служеюных сообщений чтобы потом их удалить
        await cache.aset(
            f'{message.from_user.id}-id_botmessage', 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [msg.id], 
            settings.CACHE_CREATE
        )
        
    imgs_id = await cache.aget(f'{message.from_user.id}-id_imgs')  

    if len(imgs_id) > 4:
        await limit_msg(message, bot)
        return True 
    # Если просто одна фотография
    if message.photo:
        imgs_id.append(message.photo[0].file_id)   
        await cache.aset(
            f'{message.from_user.id}-id_botmessage', 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [message.id], 
            settings.CACHE_CREATE
        )      
    else:
        msg = await bot.send_message(
            message.chat.id, 
            await get_message_text('errors', 'not_found_img'), 
            parse_mode='HTML'
        )
        # Создаем лист служеюных сообщений чтобы потом их удалить
        await cache.aset(
            f'{message.from_user.id}-id_botmessage', 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [msg.id], 
            settings.CACHE_CREATE
            )
        await bot.delete_message(message.chat.id, message.id) 
        return True       
    
    await cache.aset(
        f'{message.from_user.id}-id_imgs', 
        imgs_id,
        settings.CACHE_CREATE)

    await bot.edit_message_reply_markup(
        message.chat.id,
        await cache.aget(f'{message.from_user.id}-id_message'),
        reply_markup = await keyboard_add_chennal(message.from_user.id)
    )

#! Удалить второстепенные фото:             
async def add_channel_delete_imgs(call: types.CallbackQuery, bot: AsyncTeleBot): 
    """ call.data = add_channel_delete_imgs """
    await bot.answer_callback_query(
        call.id, 
        await get_message_text('keyboards', 'add_channel_complite'), 
    )       
    cache.delete(f'{call.from_user.id}-id_imgs') # Чистим кэш 

    await bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup = await keyboard_add_chennal(call.from_user.id)
    )

#! Изменить фото из имеющихся
async def add_channel_swap_imgs(call: types.CallbackQuery, bot: AsyncTeleBot): 
    """ call.data = add_channel_swap_imgs """
    
    id_imgs = await cache.aget(f'{call.from_user.id}-id_imgs')
    if not id_imgs:
        await bot.answer_callback_query(
            call.id, 
            await get_message_text('errors', 'cache_channel'), 
            True
        )           
        return True        
     
    i = int(call.data.split(':')[1])   
    id_img = id_imgs[i]
    # Кэш на какой картинке мы остановились    
    await cache.aset(f'{call.from_user.id}-id_img_select', i, settings.CACHE_CREATE)    
    await cache.aset(f'{call.from_user.id}-stock_img', id_img ,settings.CACHE_CREATE)
    channel = await cache.aget(f'{call.from_user.id}-channel')
    if isinstance(channel, dict) or isinstance(channel, types.ChatFullInfo):
        keyboard = await keyboard_add_chennal(call.from_user.id, i)
    else:
        keyboard = await keyboard_for_change_channel(call.from_user.id, i)

    if id_img != call.message.photo[0].file_id:
        # Создаем объект нового медиа с фото
        new_media = types.InputMediaPhoto(media=id_img, caption=call.message.caption)
        # Редактируем медиа в сообщении
        await bot.edit_message_media(
            chat_id=call.message.chat.id,
            message_id=call.message.id, 
            media=new_media,
            reply_markup=keyboard
        )


# Асинхронная функция для создания изображения с человечком
async def create_placeholder_avatar(
    size: int = 500,
    background_color: str = "white",
    line_color: str = "black",
    line_width: int = 14,
    head_radius_ratio: float = 0.4,
    eye_radius_ratio: float = 0.04,
    body_width_ratio: float = 0.7,
    body_height_ratio: float = 0.13,
    mouth_width_ratio: float = 0.3,
    mouth_height_ratio: float = 0.05
) -> io.BytesIO:
    """
    Создаёт изображение с человечком в стиле "нет аватарки".

    :param size: Размер изображения (квадратный).
    :param background_color: Цвет фона.
    :param line_color: Цвет линий для рисования.
    :param line_width: Толщина линий.
    :param head_radius_ratio: Радиус головы относительно размера изображения.
    :param eye_radius_ratio: Радиус глаз относительно размера изображения.
    :param body_width_ratio: Ширина тела относительно размера головы.
    :param body_height_ratio: Высота тела относительно размера изображения.
    :param mouth_width_ratio: Ширина рта относительно размера головы.
    :param mouth_height_ratio: Высота рта относительно размера головы.
    :return: Буфер BytesIO с изображением.
    """
    # Создаём квадратное изображение
    image = Image.new("RGB", (size, size), background_color)
    draw = ImageDraw.Draw(image)

    # Координаты центра
    center = size // 2

    # Расчёт размеров на основе пропорций
    head_radius = int(size * head_radius_ratio)
    eye_radius = int(size * eye_radius_ratio)
    body_width = int(head_radius * body_width_ratio)
    body_height = int(size * body_height_ratio)
    mouth_width = int(head_radius * mouth_width_ratio)
    mouth_height = int(head_radius * mouth_height_ratio+20)
    eye_offset_x = head_radius // 2
    eye_offset_y = head_radius // 3

    # Рисуем голову
    draw.ellipse(
        (center - head_radius, center - head_radius, center + head_radius, center + head_radius),
        outline=line_color,
        width=line_width
    )

    # Рисуем глаза
    draw.ellipse(
        (center - eye_offset_x - eye_radius, center - eye_offset_y - eye_radius,
         center - eye_offset_x + eye_radius, center - eye_offset_y + eye_radius),
        fill=line_color
    )
    draw.ellipse(
        (center + eye_offset_x - eye_radius, center - eye_offset_y - eye_radius,
         center + eye_offset_x + eye_radius, center - eye_offset_y + eye_radius),
        fill=line_color
    )

    # Рисуем рот
    draw.arc(
        (center - mouth_width, center + eye_offset_y,
         center + mouth_width, center + eye_offset_y + mouth_height),
        start=0,
        end=200,
        fill=line_color,
        width=line_width+150
    )

    # Рисуем тело
    draw.line(
        [(center, center + head_radius), (center, center + head_radius + body_height)],
        fill=line_color,
        width=line_width
    )
    draw.line(
        [(center, center + head_radius + body_height // 2),
         (center - body_width, center + head_radius + body_height)],
        fill=line_color,
        width=line_width
    )
    draw.line(
        [(center, center + head_radius + body_height // 2),
         (center + body_width, center + head_radius + body_height)],
        fill=line_color,
        width=line_width
    )

    # Сохраняем изображение в буфер
    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")
    image_buffer.seek(0)  # Перемещаем указатель в начало

    # Освобождаем ресурсы
    image.close()
    return image_buffer


#! Скачиваем файл с помощью aiohttp
async def download_pic_send(channel, bot: AsyncTeleBot, user_id, message, capti0n): 
    """
    Асинхронно отправляет фото или делает новоое фото чтобы оно было
    """
    if not await cache.aget(f'{user_id}-download-pic'):
        # Отправляем статус "отправляет фото"
        await bot.send_chat_action(message.chat.id, 'upload_photo')
        try:    # Пробуем найти фотогрвфию в канале или сообществе
            file_url = await bot.get_file_url(channel.photo.big_file_id)
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    if resp.status == 200:
                        # Читаем данные файла как байты
                        file_data = io.BytesIO(await resp.read())
                        # Сохраняем фото
                        msg = await bot.send_media_group(
                            message.chat.id, 
                            [types.InputMediaPhoto(file_data, capti0n, parse_mode='HTML')]
                        )
                    else:
                        await bot.send_message(
                            message.chat.id, 
                            await get_message_text('errors', 'not_found'), 
                            parse_mode='HTML'
                            )
        except:     # Если канал без аватарки делаем вот так
            try:
                # Создаём изображение с человечком
                image_buffer = await create_placeholder_avatar()   
                msg = await bot.send_media_group(
                    message.chat.id, 
                    [types.InputMediaPhoto(image_buffer, capti0n, parse_mode='HTML')]
                ) 
            finally:
                # Гарантируем закрытие буфера
                image_buffer.close()    
        # Обрабатываем            
        await bot.edit_message_caption(
            capti0n,
            message.chat.id,
            msg[0].id,
            reply_markup=(await keyboard_add_chennal()),
            parse_mode='HTML'
        )
        await bot.delete_message(message.chat.id, message.id)
        await cache.aset(f'{str(user_id)}-download-pic', True, 20, store_local=False)
        await cache.aset(f'{user_id}-pic', msg[0].photo[-1].file_id, settings.CACHE_CREATE)
        cache.delete(f'{message.from_user.id}-id_imgs') # Очистка если что на всякий

        return False
    else:
        await bot.send_message(
            message.chat.id, 
            await get_message_text('errors', 'wait_download'), 
            parse_mode='HTML'
            )       
        return True

#! Состояние для отправки локации
async def add_channel_location_callback(call: types.CallbackQuery, bot: AsyncTeleBot):
    """ call.data = add_channel_location """

    await set_user_state(call.from_user.id, 'add_channel_location_callback')
    msg = await bot.send_message(
        call.message.chat.id, 
        await get_message_text('general', 'add_channel_location_callback'),
        reply_markup=(await stop_message()),
        parse_mode='HTML'
        )
    # Храним id сообщения тг чтобы работать красиво
    await cache.aset(f'{call.from_user.id}-id_message', call.message.id ,settings.CACHE_CREATE)
    await cache.aset(f'{call.from_user.id}-id_botmessage', [msg.id] ,settings.CACHE_CREATE)

    
async def add_channel_location(message: types.Message, bot: AsyncTeleBot):
    """ state: add_channel_location_callback""" 

    if not await cache.aget(f'{message.from_user.id}-limitGeo'):
        if message.content_type == 'location':
            # Обработка геолокации
            city_data = await geocode(
                (message.location.latitude, message.location.longitude),
                reverse=True
                )
        elif message.content_type == 'text':
            city_data = await geocode(message.text)  
        else:
            # Ошибка нам отправили какую то парашу
            msg = await bot.send_message(
                message.chat.id, 
                await get_message_text('errors', 'not_found'), 
                parse_mode='HTML'
            )  
            # Сохраняем сообщение чтобы потом удалять их все 
            await cache.aset(
                f'{message.from_user.id}-id_botmessage', 
                await cache.aget(f'{message.from_user.id}-id_botmessage') + [msg.id] + [message.id], 
                settings.CACHE_CREATE
                )              
            return True
    else:
        msg = await bot.send_message(
            message.chat.id, 
            await get_message_text('errors', 'wait_download'), 
            parse_mode='HTML'
        )  
        # Сохраняем сообщение чтобы потом удалять их все 
        await cache.aset(
            f'{message.from_user.id}-id_botmessage', 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [msg.id] + [message.id], 
            settings.CACHE_CREATE
            )          

   # Устанавливаем лимит на 1 поиск раз в 3 секунды
    await cache.aset(f'{message.from_user.id}-limitGeo', True, 3) 

    if city_data:
        msg = await bot.send_message(
            message.chat.id, 
            str(city_data),
            reply_markup=(await complite_and_close()),
            parse_mode='HTML'
        )
        # Кэш чтобы не забыть
        await cache.aset(f'{message.from_user.id}-location', city_data, settings.CACHE_CREATE)
        # Сохраняем сообщение чтобы потом удалять их все 
        await cache.aset(
            f'{message.from_user.id}-id_botmessage', 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [msg.id] + [message.id], 
            settings.CACHE_CREATE
            )
    else:
        # Ничего не найдено
        msg = await bot.send_message(
            message.chat.id, 
            await get_message_text('errors', 'not_found'), 
            parse_mode='HTML'
        )  
        # Сохраняем сообщение чтобы потом удалять их все 
        await cache.aset(
            f'{message.from_user.id}-id_botmessage', 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [msg.id] + [message.id], 
            settings.CACHE_CREATE
            )       
        
#* Не согласен                       
async def add_channel_location_close(call: types.CallbackQuery, bot: AsyncTeleBot):
    """call.data == 'message_close'"""
    # обнуляем статус
    await set_user_state(call.from_user.id, None)

    # Удаляем
    cache.delete(f'{call.from_user.id}-location')
    # Удаляем
    msges = await cache.aget(f'{call.from_user.id}-id_botmessage')
    await bot.delete_messages(call.message.chat.id, msges)
    cache.delete(f'{call.from_user.id}-id_botmessage')

#* Если пользователь согласен с той локацией которая будет указана в анкете        
async def add_channel_location_complite(call: types.CallbackQuery, bot: AsyncTeleBot):
    """call.data == 'message_complite'"""

    # Изменияем сообщение в связи с новой информацией об анкете
    await change_message(
        bot,
        call.from_user.id,
        call.message.chat.id,
        call.message.id
    )


#! Функция для измнения текста после команды в обычный чат (смена описания или картинки поста)
async def change_message(bot: AsyncTeleBot, user_id: str|int, chat_id: str|int, msg_id: str|int, media_id: str = None, n: int=0) -> types.ChatFullInfo | Channel:
    channel = await cache.aget(f'{user_id}-channel')
    description = await cache.aget(f'{user_id}-descriptionChannal')

    if not channel: # Если пользователь тупой
        await bot.send_message(chat_id, await get_message_text('errors', 'cache_channel'), parse_mode='HTML')
        return None
    
    chage_mode = True   #* Проверка лежит ли в кэшэ обьъект Django ORM или просто json
    if isinstance(channel, dict) or isinstance(channel, types.ChatFullInfo):
        chage_mode = False

    data_city = await cache.aget(f'{user_id}-location')
    geo = data_city['address'] if data_city else None if not chage_mode else channel.region
    
    link_text = " " if chage_mode else ('\nt.me/'+str(channel.username))
    caption = anketa_text(
        channel.title, 
        description, 
        await cache.aget(f'{user_id}-folowers'),
        geo
        ) + link_text
    
    keyboard = await keyboard_add_chennal(user_id, n) if not chage_mode else await keyboard_for_change_channel(user_id, n)

    if not media_id:
        await bot.edit_message_caption(
            chat_id = chat_id,
            message_id =  await cache.aget(f'{user_id}-id_message'),
            caption = caption,
            reply_markup = keyboard,
            parse_mode='HTML'
        )
    else:
        # Если хотим и фотку измеить
        media = types.InputMediaPhoto(media_id, caption=caption, parse_mode='HTML')
        await bot.edit_message_media(
            chat_id = chat_id,
            reply_markup=keyboard,
            media = media,
            message_id = await cache.aget(f'{user_id}-id_message')
        )

    # Удаляем лишнии сообщения
    await bot.delete_message(chat_id, msg_id)
    # Удаляем все соо бота об ошибках и информации
    await bot.delete_messages(chat_id, await cache.aget(f'{user_id}-id_botmessage'))
    # Удаляем 
    cache.delete(f'{user_id}-id_botmessage') 
    await set_user_state(user_id, None)

    return channel

#! Изменения состояния чтобы отправить описание
async def add_channel_description_chat(call: types.CallbackQuery, bot: AsyncTeleBot):  #* call.data = add_channel_description_chat
    await set_user_state(call.from_user.id, 'add_channel_description_chat')
    msg = await bot.send_message(
        call.message.chat.id, 
        await get_message_text('general', 'add_channel_description_chat'),
        reply_markup=(await stop_message()),
        parse_mode='HTML'
        )
    # Храним id сообщения тг чтобы работать красиво
    await cache.aset(f'{call.from_user.id}-id_message', call.message.id ,settings.CACHE_CREATE)
    await cache.aset(f'{call.from_user.id}-id_botmessage', [msg.id] ,settings.CACHE_CREATE)    
async def add_channel_description_chat_chat(message: types.Message, bot: AsyncTeleBot):    
    #* Отправка самому описания в чат
    if message.text:
        await cache.aset(f'{message.from_user.id}-descriptionChannal', message.text, settings.CACHE_CREATE)
        # Изменияем сообщение в связи с новой информацией об анкете
        channel = await change_message(
            bot,
            message.from_user.id,
            message.chat.id,
            message.id
        )
        if not channel:
            return True # Выходим

    else:
        msg = await bot.send_message(message.chat.id, 
                                     await get_message_text('errors', 'not_found'), 
                                     parse_mode='HTML') 
                # Создаем лист служеюных сообщений чтобы потом их удалить
        await cache.aset(
            f'{message.from_user.id}-id_botmessage', 
            await cache.aget(f'{message.from_user.id}-id_botmessage') + [msg.id], 
            settings.CACHE_CREATE
            )
        await bot.delete_message(message.chat.id, message.id)  
        
#! Выпадающий список хэштегов тех же категорий канала
async def callback_add_channel_categories(call: types.CallbackQuery, bot: AsyncTeleBot, page=1):
    #call.data == "add_channel_categories"
    list_complite_ids = await cache.aget(f'{call.from_user.id}-list_complite_ids')

    category = await category_cache() 

    keyboard = await generate_paginated_keyboard(
        category, 
        page = page, 
        page_size = 5, 
        callback_prefix = 'categories',
        selected_ids = list_complite_ids if list_complite_ids else [],
        text_info = await get_message_text('keyboards', 'add_channel_complite_info')
        )
    # Кнопка в конец чтобы завершить все
    keyboard.row(
        types.InlineKeyboardButton(
            await get_message_text('keyboards', 'add_channel_back'), 
            callback_data='add_channel_back'),        

        types.InlineKeyboardButton(
            await get_message_text('keyboards', 'add_channel_complite'), 
            callback_data='add_channel_complite')
        )
    await bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup=keyboard
    )
    await bot.answer_callback_query(
        call.id,
        await get_message_text('keyboards', 'add_channel_complite_info')
    )
#! Отработка нажатия на эллементы
async def callback_categories_add(call: types.CallbackQuery, bot: AsyncTeleBot):
    data = call.data.split(":")
    action = int(data[1]) # Выбраный эллемнт
    page = int(data[3])   # СТранца
    # Вспоминаем
    list_complite_ids = await cache.aget(f'{call.from_user.id}-list_complite_ids')
    if list_complite_ids: 
        if action in list_complite_ids: # Удаляем
            list_complite_ids.remove(action)
        else:   # Добавляем
            #! Премиум фича
            premium = await cache.aget(f'{call.from_user.id}-premium') 
            lenght = len(list_complite_ids) 
            if (premium and lenght > 7) or (lenght > 3 and not premium):
                # Если у нас лимит 
                await bot.answer_callback_query(
                    call.id,
                    await get_message_text('errors', 'limit_categories'),
                    show_alert=True
                )
                return True # Выходим

            list_complite_ids.append(action)
    else:
        list_complite_ids = [action]
    await cache.aset(f'{call.from_user.id}-list_complite_ids', list_complite_ids, settings.CACHE_CREATE)
    if len(data) == 5:
        # Если это редактирование канала а не добавление нового:
        await callback_change_channel_categories(call, bot, page)
    else:    
        await callback_add_channel_categories(call, bot, page)    
    
#* Возращение назад к старой клавиатуре    
async def add_channel_back_callback(call: types.CallbackQuery, bot: AsyncTeleBot):
    """call.data == add_channel_back"""
    await bot.edit_message_caption(
        caption=call.message.caption,
        chat_id=call.message.chat.id,
        message_id=call.message.id,
        reply_markup=await keyboard_add_chennal(call.from_user.id),
        parse_mode='HTML'
    )

#* Проверка не добавлен ли канал другим пользователем
async def check_channel(channel_id):
    obj = []
    # Django orm pizdec костыль посчитать 
    async for chan in Channel.objects.filter(external_id=channel_id):
        obj.append(chan)

    if obj:
        channel = await Channel.objects.aget(external_id=channel_id)
        users = []
        async for user in channel.user.all():
            users.append(f'\n tg://user?id={user.external_id}')
        # Возращаем текст с теми кто добавил канал
        return await get_message_text("errors", "bot_add_channel_added") + f'{''.join(users)}'
    
    # Возращаем False если канала все таки нету в базе еще 
    return False
 

#! Добавление через админа тгк
async def callback_add_channel_parsing(call: types.CallbackQuery, bot: AsyncTeleBot):   #* call.data = add_channel_parsing
    await bot.send_message(
        chat_id=call.message.chat.id,
        text=await get_message_text('general', 'add_channel_parsing'),
        parse_mode="HTML",
        reply_markup=await stop_message(),
    )
    await set_user_state(call.from_user.id, "add_channel_parsing")    
async def add_channel_parsing(message: types.Message, bot: AsyncTeleBot):
    # Разбиваем ссылку
    # Регулярное выражение для извлечения username
    match = re.search(r'(?:t\.me/|telegram\.me/|@)([a-zA-Z0-9_]{5,})', message.text)
    if match:
        channel_id = f"@{match.group(1)}"
    else:
        await bot.send_message(
            chat_id=message.chat.id,
            text=await get_message_text("errors", "access_channel_link"),
            parse_mode="HTML",
        )
        return True # чтобы ошибок не было  

    try:
        # Проверяем, существует ли канал
        channel = await bot.get_chat(channel_id)
    except:
        await bot.send_message(
            chat_id=message.chat.id,
            text=await get_message_text("errors", "access_channel"),
            parse_mode="HTML",
        )
        return True # чтобы ошибок не было  
    
    # Проверяем, является ли пользователь администратором
    try:
        admins = await bot.get_chat_administrators(channel_id)
    except:
        await bot.send_message(
            chat_id=message.chat.id,
            text=await get_message_text("errors", "bot_add_channel"),
            parse_mode="HTML",
        ) 
        return True # чтобы ошибок не было
    
    # Список админов канала
    is_admin = any(admin.user.id == message.from_user.id for admin in admins)
    if is_admin:
        # Проверяем вдруг канал уже добавил кто то 
        error_add_channel = await check_channel(channel.id)
        if error_add_channel:       
            await bot.send_message(
                chat_id=message.chat.id,
                text=error_add_channel, # Показываем кто добавил этот канал
                parse_mode="HTML",
            )            
            return True # чтобы ошибок не было    

        # Сохраняем данные о канале во временное хранилище (Redis или кэш)
        cache.set(f"{message.from_user.id}-channel", channel, settings.CACHE_CREATE)
        # Описание в кэш чтобы не было багов если что
        cache.set(f'{message.from_user.id}-descriptionChannal', channel.description, settings.CACHE_CREATE)

        folowers = await bot.get_chat_member_count(channel.id)       
        await cache.aset(f'{message.from_user.id}-folowers', folowers, settings.CACHE_CREATE)

        wait = await download_pic_send(
            channel, 
            bot, 
            message.from_user.id, 
            message,
            anketa_text(
                channel.title, 
                channel.description, 
                folowers
                ) + f'\nt.me/{channel.username}',
            )
        if wait:
            await set_user_state(message.from_user.id, None)
    else:
        await bot.send_message(
            chat_id=message.chat.id,
            text=await get_message_text("errors", "is_admin_channel"),
            parse_mode="HTML",
        )


#! Финальное добавление и проверка в базу
async def callback_add_channel_complite(call: types.CallbackQuery, bot: AsyncTeleBot):
    # Категории канала
    list_complite_ids = await cache.aget(f'{call.from_user.id}-list_complite_ids')
    if not list_complite_ids:
        await bot.answer_callback_query(
            call.id,
            await get_message_text('errors', 'no_categories'),
            show_alert=True
        )
        return True
    
    title = await extract_text(call.message.caption, '>', '\n')
    description = await extract_text(call.message.caption, '⊹ ', ' ⊹')

    error = await ban_words_cheking(f'{title} \n{description}')
    if error:
        await bot.answer_callback_query(
            call.id, # Вывод ошибки в лицо
            error
        )
        text = call.message.caption + '\r\n\r\n' + error
        keyboard = await update_keyboard_warning(call, 'add_channel_complite', 2) # Доп отдача юзеру
        # Показываем ошибку путем изменения текста в анкете
        if not (error in call.message.caption.split('\n')) and call.message.reply_markup != keyboard:
            await bot.edit_message_caption(
                caption = text,
                chat_id=call.message.chat.id,
                message_id=call.message.id,
                reply_markup=keyboard,
                parse_mode='html'
            )
        return True

    await bot.delete_message(call.message.chat.id, call.message.id)
    # Получаем канал из текста анкеты
    link_channel = '@' + await extract_link(call.message.caption)
    channel = await bot.get_chat(link_channel) 
    # Проверяем вдруг канал уже добавил кто то 
    error_add_channel = await check_channel(channel.id)
    if error_add_channel:       
        await bot.send_message(
            chat_id=call.message.chat.id,
            text=error_add_channel, # Показываем кто добавил этот канал
            parse_mode="HTML",
        )            
        return True # чтобы ошибок не было 
        
    city_data = await cache.aget(f'{call.from_user.id}-location')
    
    # Несколько фото профиля
    id_imgs = await cache.aget(f'{call.from_user.id}-id_imgs')
    if id_imgs:
        poster = ' '.join(id_imgs)
    else:
        poster = call.message.photo[-1].file_id

    #TODO СДЕЛАТЬ ПРОВЕРКУ НА ПОДПИЩЕКОВ!!

    chennal_obj = await Channel.objects.acreate(
        title = title,
        poster = poster,
        external_id = channel.id,
        folowers = await bot.get_chat_member_count(link_channel)
    )
    if description:  # Описание если оно имеется
        chennal_obj.description =  description

    await chennal_obj.user.aset(
        [await User.objects.aget(external_id=call.from_user.id)]
        )
    # Добавляем тэги(категории)
    category_added = []
    for category in await category_cache():
        for id in list_complite_ids:
            if category.id == id:
                category_added.append(category) 
    await chennal_obj.categories.aset(category_added) # Финально перезаписываем

    if city_data:
        # Добавляем регион/город
        chennal_obj.region = city_data['address']
        chennal_obj.location = Point(city_data['longitude'], city_data['latitude'])
        await chennal_obj.asave()

    await bot.send_message(
        chat_id=call.message.chat.id,
        text=await get_message_text('general', 'add_complite'),
        reply_markup=await murkup_keboard_stay(),
        parse_mode="HTML",
    )            
    # Очистка кэша:
    cache.delete(f'{call.from_user.id}-channel')
    cache.delete(f'{call.from_user.id}-descriptionChannal')
    cache.delete(f'{call.from_user.id}-id_message')
    cache.delete(f'{call.from_user.id}-id_botmessage')
    cache.delete(f'{call.from_user.id}-list_complite_ids')
    cache.delete(f'{call.from_user.id}-location')
    cache.delete(f'{call.from_user.id}-stock_img')
    cache.delete(f'{call.from_user.id}-id_img_select')



#! !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! test
async def test_rabbit(message: types.Message, bot: AsyncTeleBot):
    await cache.aset(f'{message.from_user.id}-premium', False, 60*2+1, False)

    cache.clear() #! ЧИстим тест

    return True
    from mainBot.rabbitmq_service import rabbitmq_client
    text = message.text.split()[1]
    await bot.send_message(message.chat.id, "началось...")
    
    # Отправляем задачу
    # Данные задачи
    task_data = {'task': 'compute', 'data': text}

    await rabbitmq_client.connect()  # Убедитесь, что соединение установлено
    await rabbitmq_client.send_task(task_data)


