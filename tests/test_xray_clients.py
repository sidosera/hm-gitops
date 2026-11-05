"""Account lifecycle: preserve credentials, repeat safely, export the selected user."""
import copy
import importlib.util
from pathlib import Path
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

accounts = load('xray_clients')
profiles = load('xray_share_url')
probe = load('provision_vpn_probe')


def inbound(tag, network='raw', email='existing@example.org', ident='existing-id'):
    return {'tag':tag, 'protocol':'vless', 'settings':{'clients':[{'id':ident, 'email':email}]},
            'streamSettings':{'network':network, 'security':'reality' if network=='raw' else 'none',
                              'realitySettings':{'serverNames':['example.org'], 'shortIds':['1234']},
                              'xhttpSettings':{'path':'/test'}}}


class ClientLifecycleTests(unittest.TestCase):
    def test_new_client_has_one_generated_uuid_across_public_transports_and_repeat_is_noop(self):
        original = {'a':{'inbounds':[inbound('gateway')]},
                    'b':{'inbounds':[inbound('reality-legacy'),inbound('reality'),inbound('https-backup','xhttp')]}}
        snapshot = copy.deepcopy(original)
        updated = accounts.add_client(original, 'new@example.org', ['a','b'])
        self.assertEqual(original, snapshot)
        ids = {c['id'] for config in updated.values() for i in accounts.public_inbounds(config)
               for c in i['settings']['clients'] if c['email']=='new@example.org'}
        self.assertEqual(len(ids),1)
        self.assertEqual(uuid.UUID(next(iter(ids))).version,4)
        self.assertEqual(updated['b']['inbounds'][1], original['b']['inbounds'][1])
        self.assertEqual(updated, accounts.add_client(updated, 'new@example.org', ['a','b']))
        for zone in original:
            for old, new in zip(original[zone]['inbounds'],updated[zone]['inbounds']):
                self.assertEqual(old['settings']['clients'],new['settings']['clients'][:1])
        backup = updated['b']['inbounds'][2]['settings']['clients'][-1]
        self.assertNotIn('flow',backup)

    def test_granting_another_zone_reuses_identity_and_ambiguous_email_is_rejected(self):
        configs={'a':{'inbounds':[inbound('gateway')]},'b':{'inbounds':[inbound('gateway',email='other@example.org')]}}
        updated=accounts.add_client(configs,'existing@example.org',['b'])
        self.assertEqual(updated['b']['inbounds'][0]['settings']['clients'][-1]['id'],'existing-id')
        configs['b']['inbounds'][0]['settings']['clients']=[{'email':'existing@example.org','id':'different-id'}]
        with self.assertRaises(ValueError):accounts.add_client(configs,'existing@example.org',['a'])
        for email,zones in [('bad',['a']),('probe@service-account',['a']),('person@example.org',['missing'])]:
            with self.assertRaises(ValueError):accounts.add_client(configs,email,zones)

    def test_service_migration_only_changes_known_labels_and_probe_reuses_legacy_id(self):
        original={'a':{'inbounds':[inbound('gateway',email='probe-e2e@local'),
                                  inbound('relay',email='spb-relay@local',ident='relay-id'),inbound('user')]}}
        updated=accounts.migrate_service_labels(original)
        expected=copy.deepcopy(original)
        expected['a']['inbounds'][0]['settings']['clients'][0]['email']='probe-e2e@service-account'
        expected['a']['inbounds'][1]['settings']['clients'][0]['email']='spb-relay@service-account'
        self.assertEqual(updated,expected)
        self.assertEqual(updated,accounts.migrate_service_labels(updated))
        configured,ident=probe.add_account(original['a'])
        self.assertEqual(ident,'existing-id')
        self.assertEqual(configured['inbounds'][0]['settings']['clients'][0]['email'],probe.PROBE_EMAIL)

    def test_export_selects_requested_account_instead_of_first_client(self):
        config={'inbounds':[inbound('gateway')]}
        config['inbounds'][0]['settings']['clients'][0]['flow']='xtls-rprx-vision'
        config['inbounds'][0]['settings']['clients'].append({'id':'selected-id','email':'new@example.org','flow':'xtls-rprx-vision'})
        urls=profiles.export_profiles(config,['203.0.113.1'],'public-key','example.org','new@example.org')
        self.assertTrue(urls)
        self.assertTrue(all(url.startswith('vless://selected-id@') for url in urls.values()))
        self.assertEqual({},profiles.export_profiles(config,['203.0.113.1'],'public-key','example.org','missing@example.org'))


if __name__=='__main__':unittest.main()
