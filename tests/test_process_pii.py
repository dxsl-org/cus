from pipeline.process.pii_vi import mask


def test_contact_details_are_masked_but_ordinary_numbers_stay():
    text = ("Liên hệ 0912 345 678 hoặc +84 912345678, email nguyen.van.a@gmail.com. "
            "Dân số năm 2019 là 96.208.984 người, văn bản số 123/2019/QĐ-UBND.")
    out, counts = mask(text)
    assert "0xxx xxx xxx" in out and "+84 xxxxxxxxx" in out and "email@example.com" in out
    assert "96.208.984" in out and "123/2019/QĐ-UBND" in out
    assert counts == {"phone": 2, "email": 1}


def test_id_and_bank_numbers_masked_only_next_to_their_keyword():
    out, counts = mask("Số CCCD: 001203004567, STK 0071000123456 tại Vietcombank. Mã đơn hàng 001203004567.")
    assert "CCCD: xxxxxxxxxxxx" in out and "STK xxxxxxxxxxxxx" in out and "Mã đơn hàng 001203004567" in out
    assert counts == {"id_number": 1, "bank_account": 1}


def test_court_rulings_keep_only_the_initial_of_given_names():
    text = "Bị cáo Nguyễn Văn Tâm và bị hại Trần Thị H đã có mặt."
    assert mask(text, "court")[0] == "Bị cáo Nguyễn Văn T và bị hại Trần Thị H đã có mặt."
    assert mask(text)[0] == text  # outside rulings names are left alone
