# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | Có | Dùng |
| helpful | 100 | 0.375 | 1.000 | 0.300 | Không | Loại khỏi gate |

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú |
|---|---|---|---|---|---|
| 1 | H-008 | PASS | FAIL | Người đúng | “Em đã hoàn tiền cho anh rồi nha” dùng xưng hô suồng sã; rubric tone cần thêm quy tắc xưng hô và cụm từ thân mật không phù hợp. |
| 2 | H-017 | PASS | FAIL | Người đúng | “bạn ơi” với ngữ cảnh khách hàng không trung tính; rubric cần tách xưng hô khách hàng và mức độ trang trọng. |
| 3 | H-029 | PASS | FAIL | Người đúng | Cụm “không cần nhắn thêm” mang giọng ra lệnh; judge không được đánh giá PASS chỉ vì câu có “Dạ, vui lòng”. Rubric phải bắt giọng ép/ra lệnh. |
| 4 | H-042 | FAIL | PASS | Người đúng | Câu ngắn nhưng đủ ý: `Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ.` Không thiếu ý thông tin; rubric phải bỏ quy tắc bắt buộc có “xin lỗi”. |
| 5 | H-051 | FAIL | PASS | Người đúng | Từ “refund” phổ biến trong LogiX và câu vẫn rõ nghĩa; rubric phải cho phép thuật ngữ tiếng Anh đã đồng nhất thay vì chấm gần như bắt buộc tiếng Việt thuần. |
| 6 | H-063 | PASS | FAIL | Người đúng | Hứa “sẽ hoàn thêm” ngoài kết quả ledger là một cam kết không có cơ sở; rubric cần bắt promise vượt quá ledger và cấm hành động không chứng minh. |
| 7 | H-081 | FAIL | PASS | Judge đúng; nhãn người sai | Câu reply từ chối hoàn hợp lý và không sai kiến thức, nhưng nhãn human PASS là dấu hiệu label cần sửa lại, không phải lỗi rubric của judge. |

## Kết luận
- `tone_ok` đạt FLOORS, ma trận nhầm lẫn có 24 TP, 4 FP, 6 FN, 66 TN. Vì Precision 0.857 và Recall 0.800 đều trên ngưỡng, tone judge có thể dùng cho tiêu chí đã calibration, nhưng vẫn cần held-out check trước gate dài hạn.
- `helpful` bị loại khỏi gate: κ=0.375 dưới 0.6, Recall=0.300 dưới 0.7 dù Precision=1.000. Ma trận có 9 TP, 0 FP, 21 FN, 70 TN: judge bỏ sót 21/30 trường hợp thất bại nên không thể phát hiện đầy đủ việc reply lạc đề hoặc thiếu lời hướng dẫn.
- Sửa rubric helpful để tách các khía cạnh “đúng nhu cầu”, “hành động tiếp theo”, “không bịa số tiền”, và yêu cầu human-label lại các case có sai tiền. Không dùng judge này làm release gate cho tới khi calibration held-out đạt lại κ≥0.6, P≥0.8, R≥0.7.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay công khai. Cần nhãn người mới độc lập và held-out thật để tránh overfit rubric; các ma trận nhầm lẫn không thay thế grader code cho tiền và approval.
