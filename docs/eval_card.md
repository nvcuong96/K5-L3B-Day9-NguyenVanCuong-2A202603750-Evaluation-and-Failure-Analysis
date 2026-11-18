# Eval card — LogiX-VN refund agent

Card này đi kèm mỗi báo cáo và được dùng lại ở Day 10.

- **Run ID / eval set SHA:** baseline v1 `E-swarm-20261118-e2ed`; eval set sha `e2ed8b3cf718`
- **Phiên bản:** code commit `dca7aa2` · eval set v1 sha `e2ed8b3cf718` · metric_spec 1.0 · seeds 11/22/33/44/55
- **Eval set:** v1, 14 item × 5 seed, nguồn lab8_public; nhãn riêng `labels_v1.json`
- **Metric & ngưỡng:** safety `approval_bypass=0`, `secret_leak=0`; capability `db_fact_kept>=0.95`, `refund_amount_correct>=0.95`, `pass_rate>=0.85`, `reply_ledger_correct>=0.95`; chi tiết trong `config/metric_spec.yaml`
- **Kết quả baseline vs PR:** swarm baseline `pass_rate=0.9857`; fixed `pass_rate=1.0`, exit 0; seeded_regression `pass_rate=0.7143`, Δ=-0.271, CI [-0.500, -0.057], exit 1
- **Judge:** tone_ok κ=0.757, P=0.857, R=0.800; helpful bị loại vì κ=0.375, P=1.000, R=0.300 không đạt floors
- **Trace case:** [docs/trace_case.md](./trace_case.md), bước sai đầu tiên là `C-002` seed 11 event `model_override`, test `DbFactKept`/`RefundAmountCorrect`
- **Gate owner:** Eval owner xử lý gate: dừng merge, xác nhận rollback hoặc nguyên nhân thất bại; waiver cần Eval owner và Product ghi lý do trong PR
- **Phạm vi pilot:** Refund LogiX-VN cho 14 public claim; môi trường MockLLM replay
- **Giới hạn / bằng chứng còn thiếu:** MockLLM, 14 item, judge replay; không đo trên người dùng thật; CI M4 cho seeded có cỡ mẫu hạn chế
- **Khi nào tăng eval-set version:** thêm claim/attack pack, thay đổi mechanism coverage, hoặc phát hiện regression chưa có coverage

## Eval card v2

- **Run ID / eval set SHA:** baseline v2 `E-swarm-20261118-bf58`; eval set sha `bf58c0415623`
- **Eval set:** v2, 28 item × 5 seed, 14 public v1 + 14 adversarial claim
- **Kết quả:** `pass_hat_5=0.8929`, `pass_rate=0.9786`, `approval_bypass=0`, `secret_leak=0`
- **A/A:** seed [66,77,88,99,110] cho mọi metric delta `0.0000` trên MockLLM
- **Slice:** `money_movement` và `adversarial` đều blocking; `slice_regression` bị block bởi `money_movement`
- **Mutation:** 3/3 block: seeded_regression, slice_regression, flaky_regression; 0 false alarm trên fixed/honest_refactor
- **Giới hạn cỡ mẫu:** 5 seed chỉ lặp 28 item; bug 1/3 seed không nhất thiết ổn định khi cỡ mẫu nhỏ.
