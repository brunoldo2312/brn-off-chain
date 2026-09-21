import os
os.environ.setdefault("BRN_WEB_PASS", "test_only")

def test_blockchain_genesis():
    from bruno_blockchain_real import Blockchain
    bc = Blockchain()
    assert len(bc.chain) >= 1
    assert bc.chain[0].index == 0

def test_wallet_import():
    import cripto_wallet  # noqa

def test_web_server_import():
    import web_server  # noqa
