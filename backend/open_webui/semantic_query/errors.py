from __future__ import annotations

ERROR_MESSAGES = {
    'DB-COMPANY-CONTEXT-MISSING': '目前請求缺少企業身分，無法判斷可使用的資料來源。',
    'DB-CONNECTOR-DISABLED': '資料庫連接器不存在或目前已停用。',
    'DB-MODEL-NOT-ALLOWED': '目前模型未獲授權使用這個資料庫連接器。',
    'DB-CHANNEL-NOT-ALLOWED': '目前通訊渠道未獲授權使用這個資料庫連接器。',
    'DB-USER-NOT-ALLOWED': '目前使用者或群組未獲授權使用這個資料庫連接器。',
    'DB-CONNECTION-FAILED': '目前無法連線到企業資料庫，請稍後再試。',
    'DB-AUTHENTICATION-FAILED': '企業資料庫認證失敗，請由管理員檢查連線憑證。',
    'DB-TIMEOUT': '企業資料庫查詢逾時，請縮小查詢範圍後再試。',
    'SCHEMA-SNAPSHOT-NOT-READY': '資料庫結構尚未完成掃描。',
    'SEMANTIC-DATASET-NOT-FOUND': '找不到指定的企業資料集。',
    'SEMANTIC-DATASET-NOT-PUBLISHED': '企業資料集尚未發布。',
    'SEMANTIC-DATASET-NOT-ALLOWED': '目前使用者、模型、渠道或工作流未獲授權使用這個資料集。',
    'SEMANTIC-FIELD-NOT-ALLOWED': '查詢使用了未授權或未發布的資料欄位。',
    'SEMANTIC-RELATIONSHIP-MISSING': '資料表之間尚未設定可用關聯。',
    'SEMANTIC-RELATIONSHIP-AMBIGUOUS': '資料表之間存在多條關聯，請由管理員確認查詢路徑。',
    'SEMANTIC-MODEL-DRIFT-BLOCKED': '資料庫結構已變更，這個資料集需由管理員重新確認。',
    'ROW-POLICY-CONTEXT-MISSING': '缺少套用資料列權限所需的使用者或渠道資訊。',
    'QUERY-PLAN-INVALID': '查詢計畫格式不正確。',
    'QUERY-TYPE-MISMATCH': '查詢條件與欄位型別不相容。',
    'QUERY-FANOUT-RISK': '這個跨表聚合可能重複計算資料，已停止執行。',
    'QUERY-COST-LIMIT': '查詢超出允許的複雜度或結果限制。',
    'QUERY-DAILY-LIMIT': '今日語意查詢額度已用完，請明日再試或聯絡管理員調整方案。',
    'SEMANTIC-DATASET-LIMIT': '語意資料集數量已達方案上限。',
    'SEMANTIC-PLAN-INACTIVE': '企業方案目前無法使用語意資料查詢。',
    'SEMANTIC-ENTITLEMENT-UNAVAILABLE': '目前無法驗證企業方案，為保護資料已暫停查詢。',
    'QUERY-COST-REVIEW-REQUIRED': '此查詢可能掃描大量資料，需由管理員調整資料模型或範圍。',
    'QUERY-RESULT-LIMIT': '查詢結果超過允許大小。',
    'QUERY-COMPILATION-FAILED': '系統無法將查詢計畫轉換為安全查詢。',
    'QUERY-EXECUTION-FAILED': '資料庫查詢執行失敗。',
}


class SemanticQueryError(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str | None = None,
        *,
        field_path: str | None = None,
        retryable: bool = False,
        admin_action: str | None = None,
        request_id: str | None = None,
    ):
        self.code = code
        self.detail = detail or ERROR_MESSAGES.get(code, '企業資料查詢失敗。')
        self.field_path = field_path
        self.retryable = retryable
        self.admin_action = admin_action
        self.request_id = request_id
        super().__init__(self.detail)

    def public(self, request_id: str | None = None) -> dict:
        return {
            'code': self.code,
            'message': ERROR_MESSAGES.get(self.code, self.detail),
            'fieldPath': self.field_path,
            'retryable': self.retryable,
            'adminAction': self.admin_action,
            'requestId': request_id or self.request_id,
        }
