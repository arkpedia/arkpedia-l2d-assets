import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import playback

class Playback(unittest.TestCase):
    def test_controller_is_selected_from_the_prefab_tree(self):
        trees={1:('GameObject',{'m_Component':[{'component':{'m_FileID':0,'m_PathID':2}}]}),
               2:('MonoBehaviour',{'_extraTrackAnimations':[{'trackIndex':2,'animationName':'exLayer_1'}]}),
               3:('MonoBehaviour',{'_extraTrackAnimations':[{'trackIndex':3,'animationName':'other'}]})}
        self.assertEqual(playback.controller_playback([1],trees.get),{'extraTracks':[{'track':2,'name':'exLayer_1'}],'voiceActions':[]})
    def test_voice_ids_keep_the_authored_body_and_mouth_times(self):
        tree={'_extraTrackAnimations':[], '_voiceToActionIds':[{'voiceName':'CN_002','actionId':'line_1'}],
              '_lipSyncDatas':[{'actionName':'line_1','animationInfos':[{'name':'Interact_1','time':0,'blendInDuration':0},{'name':'Idle','time':7.088480407732146,'blendInDuration':0.9115195922678536}],
                              'eventInfos':[{'name':'Talk_2','time':0},{'name':'Talk_1','time':3}]}]}
        got=playback.controller_playback([1],lambda _:('MonoBehaviour',tree))
        self.assertEqual(got['voiceActions'][0]['id'],'CN_002')
        self.assertEqual(got['voiceActions'][0]['body'][1],{'name':'Idle','time':7.088480407732146,'mix':0.9115195922678536})
        playback.validate_playback(got,{'Interact_1':8,'Idle':5,'Talk_2':0.3,'Talk_1':0.3})
        with self.assertRaises(ValueError):playback.validate_playback(got,{'Idle':5})
    def test_reserved_and_duplicate_tracks_are_rejected(self):
        for tracks in [[{'track':0,'name':'x'}],[{'track':1,'name':'x'}],[{'track':2,'name':'x'},{'track':2,'name':'x'}]]:
            with self.assertRaises(ValueError): playback.validate_playback({'extraTracks':tracks,'voiceActions':[]})
    def test_no_controller_is_compatible_with_older_models(self):
        self.assertIsNone(playback.controller_playback([],lambda _:None))
