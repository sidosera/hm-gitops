"""Do not release connection instructions before the account revision is deployed."""
import copy
import base64
import contextlib
import io
import json
from email import policy
from email.parser import Parser
import yaml
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import subprocess
import tempfile
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
with patch.object(sys,'path',[str(ROOT/'scripts'),*sys.path]):
    spec=importlib.util.spec_from_file_location('delivery',ROOT/'scripts/xray_delivery.py')
    delivery=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delivery)


class DeliveryReadinessTests(unittest.TestCase):
    def setUp(self):
        self.pr={'state':'MERGED','baseRefName':'main','mergeCommit':{'oid':'merged'},
                 'files':[{'path':'conf/xray/credentials-secret.sops.yaml'}]}
        self.flux={'metadata':{'generation':3},'status':{'observedGeneration':3,
                   'lastAppliedRevision':'main@sha1:current','conditions':[{'type':'Ready','status':'True'}]}}

    def test_only_ready_deployed_revision_can_prepare_instructions(self):
        delivery.verify_delivery_state(self.pr,self.flux,'current')
        for change in [{'state':'OPEN'},{'baseRefName':'other'},{'mergeCommit':None},
                       {'files':[{'path':'README.md'}]}]:
            pr={**self.pr,**change}
            with self.assertRaises(ValueError):delivery.verify_delivery_state(pr,self.flux,'current')
        for field,value in [('observedGeneration',2),('lastAppliedRevision','main@sha1:old'),
                            ('conditions',[{'type':'Ready','status':'False'}])]:
            flux=copy.deepcopy(self.flux);flux['status'][field]=value
            with self.assertRaises(ValueError):delivery.verify_delivery_state(self.pr,flux,'current')

    def test_unmerged_pr_never_contacts_server_decrypts_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'message.eml'
            args=SimpleNamespace(pr=1,output=output)
            with patch.object(delivery.subprocess,'run',side_effect=[
                    subprocess.CompletedProcess([],0,stdout=b''),
                    subprocess.CompletedProcess([],0,stdout=b'current\n'),
                    subprocess.CompletedProcess([],0,stdout=b'{"state":"OPEN"}')]) as run:
                with self.assertRaisesRegex(ValueError,'not been merged'):
                    delivery.prepare_message(Path(directory),args)
                self.assertEqual([c.args[0][0] for c in run.call_args_list],['git','git','gh'])
                self.assertFalse(output.exists())


    def test_ready_account_creates_private_addressed_draft_without_printing_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);repo=base/'repo'
            (repo/'cluster/clusters/hm-cluster').mkdir(parents=True)
            (repo/'vm/group_vars').mkdir(parents=True)
            (repo/'cluster/clusters/hm-cluster/hosts.yml').write_text(yaml.safe_dump({
                'vm_hosts':['203.0.113.1'],'vm_node_settings':{'203.0.113.1':{'zone':'a','gateway_entry':True}}}))
            (repo/'vm/group_vars/all.yml').write_text('ansible_user: admin\n')
            config={'inbounds':[{'tag':'gateway','protocol':'vless','settings':{'clients':[
                {'id':'test-only-id','email':'person@example.org','flow':'xtls-rprx-vision'}]},
                'streamSettings':{'security':'reality','network':'raw','realitySettings':{
                    'privateKey':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
                    'serverNames':['example.org'],'shortIds':['1234']}}}]}
            secret={'data':{'a.gateway.json':base64.b64encode(json.dumps(config).encode()).decode()}}
            template=base/'template';template.write_text('Hello $email\n$profiles')
            args=SimpleNamespace(pr=1,email='person@example.org',zone=None,template=template,
                                 subject='Connection instructions',output=base/'message.eml',ssh_key=base/'key')
            replies=[b'',b'current\n',json.dumps(self.pr).encode(),b'',
                     json.dumps(self.flux).encode(),yaml.safe_dump(secret).encode()]
            captured=io.StringIO()
            with patch.object(delivery.subprocess,'run',side_effect=[
                    subprocess.CompletedProcess([],0,stdout=v) for v in replies]), \
                 contextlib.redirect_stdout(captured):
                delivery.prepare_message(repo,args)
            self.assertEqual(args.output.stat().st_mode & 0o777,0o600)
            message=Parser(policy=policy.default).parsestr(args.output.read_text())
            self.assertEqual(message['To'],'person@example.org')
            self.assertIn('vless://test-only-id@',message.get_content())
            self.assertNotIn('test-only-id',captured.getvalue())
            self.assertNotIn('person@example.org',captured.getvalue())


if __name__=='__main__':unittest.main()
