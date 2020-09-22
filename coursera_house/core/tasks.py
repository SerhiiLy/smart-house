from __future__ import absolute_import, unicode_literals

import requests
from celery import task
from django.conf import settings
from django.core.mail import EmailMessage

from .models import Setting

TOKEN = settings.SMART_HOME_ACCESS_TOKEN
url = settings.SMART_HOME_API_URL
headers = {'Authorization': f'Bearer {TOKEN}'}

@task()
def smart_home_manager():
    # Здесь ваш код для проверки условий
    controller_data = requests.get(url, headers=headers).json().get('data')
    controller_data = {x['name']: x for x in controller_data}
    payload = {
        'controllers': []
    }

    if controller_data['leak_detector']['value']:
        # если датчик показывает протечку и есть гор. и/или хол. вода,
        # перекрываем гор. и/или хол. воду
        if controller_data['cold_water']['value']:
            payload['controllers'].append({'name': 'cold_water', 'value': False})

        if controller_data['hot_water']['value']:
            payload['controllers'].append({'name': 'hot_water', 'value': False})
        email = EmailMessage(
            'leak detector',
            'text',
            settings.EMAIL_HOST,
            [settings.EMAIL_RECEPIENT],
        )
        email.send(fail_silently=False)
    # если протечка или нет холодной воды
    if (controller_data['leak_detector']['value']) or (not controller_data['cold_water']['value']):
        if controller_data['boiler']['value']:
            payload['controllers'].append({'name': 'boiler', 'value': False})
        if controller_data['washing_machine']['value'] in ('on', 'broken'):
            payload['controllers'].append({'name': 'washing_machine', 'value': "off"})

    boiler_temperature = controller_data['boiler_temperature']['value']
    hot_water_target_temperature = Setting.objects.get(
        controller_name='hot_water_target_temperature').value

    bedroom_temperature = controller_data['bedroom_temperature']['value']
    bedroom_target_temperature = Setting.objects.get(
        controller_name='bedroom_target_temperature').value

    # Если обнаружен дым (smoke_detector), немедленно выключить
    # следующие приборы [air_conditioner, bedroom_light, bathroom_light, boiler, washing_machine],
    # и ни при каких условиях не включать их, пока дым не исчезнет.
    if controller_data['smoke_detector']['value']:
        # если дым, выключаем кондиционер, бойлер и свет, но только если они включены
        if controller_data['air_conditioner']['value']:
            payload['controllers'].append({'name': 'air_conditioner', 'value': False})
        if controller_data['bedroom_light']['value']:
            payload['controllers'].append({'name': 'bedroom_light', 'value': False})
        if controller_data['bathroom_light']['value']:
            payload['controllers'].append({'name': 'bathroom_light', 'value': False})
        if controller_data['boiler']['value']:
            payload['controllers'].append({'name': 'boiler', 'value': False})
        if controller_data['washing_machine']['value'] in ('on', 'broken'):
            payload['controllers'].append({'name': 'washing_machine', 'value': "off"})

    can_turn_on = {
        'boiler': controller_data['cold_water']['value'] and not controller_data['leak_detector']['value']
                  and not controller_data['smoke_detector']['value'] and not controller_data['boiler'],
        'air_conditioner': not controller_data['smoke_detector']['value']
    }

    # 3 Если горячая вода имеет температуру (boiler_temperature) меньше чем hot_water_target_temperature - 10%,
    # нужно включить бойлер (boiler), и ждать пока она не достигнет температуры hot_water_target_temperature + 10%,
    # после чего в целях экономии энергии бойлер нужно отключить
    if boiler_temperature < hot_water_target_temperature * 0.9 and can_turn_on['boiler']:
        payload['controllers'].append({'name': 'boiler', 'value': True})
    if boiler_temperature > hot_water_target_temperature * 1.1:
        payload['controllers'].append({'name': 'boiler', 'value': False})

    # 7 Если температура в спальне (bedroom_temperature) поднялась
    # выше bedroom_target_temperature + 10% - включить кондиционер (air_conditioner),
    # и ждать пока температура не опустится ниже bedroom_target_temperature - 10%, после чего кондиционер отключить.
    if bedroom_temperature < bedroom_target_temperature * 0.9 and can_turn_on['air_conditioner']:
        payload['controllers'].append({'name': 'air_conditioner', 'value': False})
    if bedroom_temperature > bedroom_target_temperature * 1.1:
        payload['controllers'].append({'name': 'air_conditioner', 'value': True})

    #Если на улице (outdoor_light) темнее 50, открыть шторы (curtains),
    # но только если не горит лампа в спальне (bedroom_light).
    # Если на улице (outdoor_light) светлее 50, или горит свет в спальне (bedroom_light),
    # закрыть шторы. Кроме случаев когда они на ручном управлении
    outdoor_light = controller_data['outdoor_light']['value']
    bedroom_light = controller_data['bedroom_light']['value']
    if controller_data['curtains']['value'] == 'slightly_open':
        pass
    else:
        if outdoor_light < 50 and not bedroom_light:
            payload['controllers'].append({'name': 'curtains', 'value': 'open'})
        elif outdoor_light > 50 or bedroom_light:
            payload['controllers'].append({'name': 'curtains', 'value': 'close'})

    if payload['controllers']:
        unique = []
        for item in payload['controllers']:
            if item not in unique:
                unique.append(item)
        payload['controllers'] = unique
        requests.post(url, headers=headers, json=payload)
