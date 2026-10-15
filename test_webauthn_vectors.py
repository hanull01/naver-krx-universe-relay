"""Official py_webauthn example vectors; no private credential material."""
import unittest

from webauthn import base64url_to_bytes, verify_authentication_response, verify_registration_response

CID = "ZoIKP1JQvKdrYj1bTUPJ2eTUsbLeFkv-X5xJQNr4k6s"
REG_CHALLENGE = "CeTWogmg0cchuiYuFrv8DXXdMZSIQRVZJOga_xayVVEcBj0Cw3y73yhD4FkGSe-RrP6hPJJAIm3LVien4hXELg"
AUTH_CHALLENGE = "iPmAi1Pp1XL6oAgq3PWZtZPnZa1zFUDoGbaQ0_KvVG1lF2s3Rt_3o4uSzccy0tmcTIpTTT4BU1T-I4maavndjQ"
PUB = "pAEDAzkBACBZAQDfV20epzvQP-HtcdDpX-cGzdOxy73WQEvsU7Dnr9UWJophEfpngouvgnRLXaEUn_d8HGkp_HIx8rrpkx4BVs6X_B6ZjhLlezjIdJbLbVeb92BaEsmNn1HW2N9Xj2QM8cH-yx28_vCjf82ahQ9gyAr552Bn96G22n8jqFRQKdVpO-f-bvpvaP3IQ9F5LCX7CUaxptgbog1SFO6FI6ob5SlVVB00lVXsaYg8cIDZxCkkENkGiFPgwEaZ7995SCbiyCpUJbMqToLMgojPkAhWeyktu7TlK6UBWdJMHc3FPAIs0lH_2_2hKS-mGI1uZAFVAfW1X-mzKL0czUm2P1UlUox7IUMBAAE"
REG = {"id":CID,"rawId":CID,"response":{"attestationObject":"o2NmbXRkbm9uZWdhdHRTdG10oGhhdXRoRGF0YVkBZ0mWDeWIDoxodDQXD2R2YFuP5K65ooYyx5lc87qDHZdjRQAAAAAAAAAAAAAAAAAAAAAAAAAAACBmggo_UlC8p2tiPVtNQ8nZ5NSxst4WS_5fnElA2viTq6QBAwM5AQAgWQEA31dtHqc70D_h7XHQ6V_nBs3Tscu91kBL7FOw56_VFiaKYRH6Z4KLr4J0S12hFJ_3fBxpKfxyMfK66ZMeAVbOl_wemY4S5Xs4yHSWy21Xm_dgWhLJjZ9R1tjfV49kDPHB_ssdvP7wo3_NmoUPYMgK-edgZ_ehttp_I6hUUCnVaTvn_m76b2j9yEPReSwl-wlGsabYG6INUhTuhSOqG-UpVVQdNJVV7GmIPHCA2cQpJBDZBohT4MBGme_feUgm4sgqVCWzKk6CzIKIz5AIVnspLbu05SulAVnSTB3NxTwCLNJR_9v9oSkvphiNbmQBVQH1tV_psyi9HM1Jtj9VJVKMeyFDAQAB","clientDataJSON":"eyJ0eXBlIjoid2ViYXV0aG4uY3JlYXRlIiwiY2hhbGxlbmdlIjoiQ2VUV29nbWcwY2NodWlZdUZydjhEWFhkTVpTSVFSVlpKT2dhX3hheVZWRWNCajBDdzN5NzN5aEQ0RmtHU2UtUnJQNmhQSkpBSW0zTFZpZW40aFhFTGciLCJvcmlnaW4iOiJodHRwOi8vbG9jYWxob3N0OjUwMDAiLCJjcm9zc09yaWdpbiI6ZmFsc2V9"},"type":"public-key"}
AUTH = {"id":CID,"rawId":CID,"response":{"authenticatorData":"SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2MFAAAAAQ","clientDataJSON":"eyJ0eXBlIjoid2ViYXV0aG4uZ2V0IiwiY2hhbGxlbmdlIjoiaVBtQWkxUHAxWEw2b0FncTNQV1p0WlBuWmExekZVRG9HYmFRMF9LdlZHMWxGMnMzUnRfM280dVN6Y2N5MHRtY1RJcFRUVDRCVTFULUk0bWFhdm5kalEiLCJvcmlnaW4iOiJodHRwOi8vbG9jYWxob3N0OjUwMDAiLCJjcm9zc09yaWdpbiI6ZmFsc2V9","signature":"iOHKX3erU5_OYP_r_9HLZ-CexCE4bQRrxM8WmuoKTDdhAnZSeTP0sjECjvjfeS8MJzN1ArmvV0H0C3yy_FdRFfcpUPZzdZ7bBcmPh1XPdxRwY747OrIzcTLTFQUPdn1U-izCZtP_78VGw9pCpdMsv4CUzZdJbEcRtQuRS03qUjqDaovoJhOqEBmxJn9Wu8tBi_Qx7A33RbYjlfyLm_EDqimzDZhyietyop6XUcpKarKqVH0M6mMrM5zTjp8xf3W7odFCadXEJg-ERZqFM0-9Uup6kJNLbr6C5J4NDYmSm3HCSA6lp2iEiMPKU8Ii7QZ61kybXLxsX4w4Dm3fOLjmDw"},"type":"public-key"}

class OfficialWebAuthnVectors(unittest.TestCase):
    def test_registration_success_and_wrong_values_fail(self):
        result=verify_registration_response(credential=REG,expected_challenge=base64url_to_bytes(REG_CHALLENGE),expected_origin="http://localhost:5000",expected_rp_id="localhost",require_user_verification=True)
        self.assertEqual(result.credential_id,base64url_to_bytes(CID))
        for kwargs in ({"expected_challenge":b"bad"},{"expected_origin":"https://wrong.example"},{"expected_rp_id":"wrong.example"}):
            with self.assertRaises(Exception): verify_registration_response(credential=REG,expected_challenge=kwargs.get("expected_challenge",base64url_to_bytes(REG_CHALLENGE)),expected_origin=kwargs.get("expected_origin","http://localhost:5000"),expected_rp_id=kwargs.get("expected_rp_id","localhost"),require_user_verification=True)
    def test_authentication_success_counter_and_bad_signature_fail(self):
        result=verify_authentication_response(credential=AUTH,expected_challenge=base64url_to_bytes(AUTH_CHALLENGE),expected_origin="http://localhost:5000",expected_rp_id="localhost",credential_public_key=base64url_to_bytes(PUB),credential_current_sign_count=0,require_user_verification=True)
        self.assertEqual(result.new_sign_count,1)
        bad={**AUTH,"response":{**AUTH["response"],"signature":"AAAA"}}
        with self.assertRaises(Exception): verify_authentication_response(credential=bad,expected_challenge=base64url_to_bytes(AUTH_CHALLENGE),expected_origin="http://localhost:5000",expected_rp_id="localhost",credential_public_key=base64url_to_bytes(PUB),credential_current_sign_count=0,require_user_verification=True)
