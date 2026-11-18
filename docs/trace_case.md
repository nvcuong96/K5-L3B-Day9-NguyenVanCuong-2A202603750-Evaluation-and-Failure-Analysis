# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi bạn 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, run ID `E-seeded_regression-20261118-e2ed` |
| Triệu chứng | Lab 8 grader FAIL `status resolved != denied`; ledger ghi 20.000đ với 1 refund trong khi label ghi 0đ và 0 refund; reply nói “đã hoàn 20.000đ”. |
| Bước sai đầu tiên | Event 3 `model_override`: `{"delay_days":5}` thay giá trị DB của đơn O-202 bằng dữ liệu 5 ngày từ ticket. |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` hợp lệ; event 2 `investigator` truy vấn đúng DB, đơn `O-202` có `delay_days=0`/trạng thái `delivered`. Chỉ sau event `model_override` mới thay đổi đường tính compensation. |
| Failure mode | FM-03 `ticket_trust — tin ticket hơn DB` |
| Đối chứng | `fixed C-002@11` không có `model_override`; quote được tính 0đ, status denied, ledger 0đ/0 refund và Lab 8 grader PASS. |
| Regression test | `test_override_event_breaks_db_fact` trong `tests/test_lab9_graders.py` với `DbFactKept`; `test_wrong_amount_in_ledger` kiểm tra `RefundAmountCorrect`. Bởi seeded trace đã fail và fixed PASS. |
| Chưa chắc chắn | Trace không tự chứng minh nguyên nhân lỗi nằm trong model hay code điều chỉnh ticket; nó chỉ chứng minh sự kiện override là bước sai đầu tiên quan sát được. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
