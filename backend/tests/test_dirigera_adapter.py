"""Adapter contract tests use installed DIRIGERA models, never a network hub."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from app.providers import DirigeraProvider
from app.models import SceneCreate


def test_adapter_contract():
    light = NS(id='a', room=NS(id='room', name='Study'), is_reachable=True,
               capabilities=NS(can_receive=['isOn', 'lightLevel']),
               attributes=NS(custom_name='Lamp', is_on=True, light_level=60,
                             color_temperature=None, color_hue=None, color_saturation=None),
               set_light=Mock(), set_light_level=Mock())
    scene = NS(id='scene', info=NS(name='Saved'), trigger=Mock())
    provider = DirigeraProvider.__new__(DirigeraProvider)
    provider.hub = Mock()
    provider.hub.get_lights.return_value = [light]
    provider.hub.get.return_value = [{'id': 'scene', 'info': {'name': 'Saved'}}]
    assert provider.list_lights()[0].room == 'Study'
    provider.set_power('a', False)
    light.set_light.assert_called_once_with(False)
    provider.set_brightness('a', 55)
    light.set_light_level.assert_called_once_with(55)
    provider.activate_scene('scene')
    provider.hub.post.assert_called_once_with(route='/scenes/scene/trigger')
    provider.hub.post.reset_mock()
    provider.hub.post.return_value = {'id': 'scene'}
    assert provider.create_scene(SceneCreate(name='Saved', light_ids=['a'])).id == 'scene'
    args = provider.hub.post.call_args.kwargs
    assert args['route'] == '/scenes/'
    assert args['data']['info']['name'] == 'Saved'
    assert args['data']['actions'][0] == {
        'id': 'a', 'deviceId': 'a', 'type': 'device', 'enabled': True, 'attributes': {'isOn': True}}


def test_scene_discovery_accepts_new_hub_types_and_icons():
    provider = DirigeraProvider.__new__(DirigeraProvider)
    provider.hub = Mock()
    provider.hub.get.return_value = [
        {'id': 'manual', 'type': 'userScene', 'info': {'name': 'Reading', 'icon': 'scenes_book'}},
        {'id': 'wind-down', 'type': 'windDownScene', 'info': {'name': 'Wind down', 'icon': 'future_icon'},
         'triggers': [{'future': 'format'}], 'actions': [{'future': 'format'}]},
        {'id': 'wake-up', 'type': 'wakeUpScene', 'info': {'name': 'Wake up'}},
    ]
    assert [(scene.id, scene.name) for scene in provider.list_scenes()] == [
        ('manual', 'Reading'), ('wind-down', 'Wind down'), ('wake-up', 'Wake up')]
    provider.hub.get.assert_called_once_with('/scenes')
    provider.hub.get_scenes.assert_not_called()
    provider.hub.post.assert_not_called()


def test_new_scene_type_activation_uses_existing_trigger_endpoint():
    provider = DirigeraProvider.__new__(DirigeraProvider)
    provider.hub = Mock()
    provider.hub.get.return_value = [
        {'id': 'wind-down', 'type': 'windDownScene', 'info': {'name': 'Wind down'}}]
    provider.activate_scene('wind-down')
    provider.hub.post.assert_called_once_with(route='/scenes/wind-down/trigger')


def test_unknown_scene_never_sends_command():
    import pytest
    from app.providers import NotFound
    provider = DirigeraProvider.__new__(DirigeraProvider)
    provider.hub = Mock()
    provider.hub.get.return_value = [{'id': 'known', 'info': {'name': 'Known'}}]
    with pytest.raises(NotFound):
        provider.activate_scene('unknown')
    provider.hub.post.assert_not_called()


def test_scene_ids_are_encoded_before_url_construction():
    provider = DirigeraProvider.__new__(DirigeraProvider)
    provider.hub = Mock()
    provider.hub.get.return_value = [{'id': 'scene/with?special', 'info': {'name': 'Special'}}]
    provider.activate_scene('scene/with?special')
    provider.hub.post.assert_called_once_with(route='/scenes/scene%2Fwith%3Fspecial/trigger')


def test_malformed_scene_metadata_returns_sanitized_api_error():
    from fastapi.testclient import TestClient
    from app.main import create_app
    provider = DirigeraProvider.__new__(DirigeraProvider)
    provider.hub = Mock()
    provider.hub.get_lights.return_value = []
    provider.hub.get.return_value = [{'id': 'scene', 'info': {'icon': 'private-value'}}]
    with TestClient(create_app(provider)) as client:
        response = client.get('/api/scenes')
        assert response.status_code == 502
        assert 'private-value' not in response.text
        assert 'unsupported' in response.json()['detail']
    provider.hub.post.assert_not_called()
