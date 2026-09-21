# assets.py — Registro de ativos do mundo real (RWA) + KYC/AML.
#
# Conceitos:
#   AssetDefinition  → metadados legais/financeiros do ativo tokenizado
#   ComplianceRecord → status KYC/AML de um endereço (inclui sanções)
#   AssetRegistry    → regras de negócio (emitir, transferir, congelar)
#
# Thread-safe: todos os métodos públicos pegam self._lock.

from __future__ import annotations
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

# ==================================================================
# Constantes
# ==================================================================
ASSET_TYPES = {
    "currency", "stablecoin", "equity", "etf", "bond", "fund",
    "derivative", "real_estate", "commodity", "invoice",
    "carbon", "other",
}

KYC_STATUS = {"pending", "approved", "rejected", "expired",
              "revoked", "sanctioned"}

KYC_LEVELS = {"basic", "accredited", "professional", "institutional"}

_LEVEL_ORDER = {"basic": 0, "accredited": 1, "professional": 2,
                "institutional": 3}

SANCTION_LISTS = {"ofac", "eu", "un", "uk", "br_coaf"}

_REGULATORY_STATUS = {"registered", "exempt", "unregistered"}

_ASSET_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")

_MUTABLE_ASSET_FIELDS = {
    "name", "symbol", "custodian", "isin", "lei", "cusip",
    "max_supply", "transfer_restricted", "legal_doc_hash",
    "offering_memo_hash", "metadata", "transfer_agent", "frozen",
}


def _valid_address(a: str) -> bool:
    return bool(a) and isinstance(a, str) and a.startswith("brn1") and len(a) == 44


# ==================================================================
# Definição do ativo
# ==================================================================
@dataclass
class AssetDefinition:
    asset_id: str
    name: str
    symbol: str
    asset_type: str
    decimals: int
    issuer: str
    transfer_agent: str
    custodian: str = ""
    isin: str = ""
    lei: str = ""
    cusip: str = ""
    regulatory_status: str = "unregistered"
    max_supply: float = 0.0
    transfer_restricted: bool = True
    legal_doc_hash: str = ""
    offering_memo_hash: str = ""
    created_at: float = field(default_factory=time.time)
    frozen: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AssetDefinition":
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})


# ==================================================================
# Registro de compliance (KYC/AML)
# ==================================================================
@dataclass
class ComplianceRecord:
    address: str
    status: str = "pending"
    level: str = "basic"
    jurisdiction: str = ""
    verified_by: str = ""
    verified_at: float = 0.0
    expires_at: float = 0.0
    document_hash: str = ""
    sanctions: List[str] = field(default_factory=list)
    restrictions: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def is_sanctioned(self) -> bool:
        return self.status == "sanctioned" or bool(self.sanctions)

    def is_valid(self, now: Optional[float] = None) -> bool:
        if self.is_sanctioned():
            return False
        if self.status != "approved":
            return False
        if self.expires_at and (now or time.time()) > self.expires_at:
            return False
        return True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ComplianceRecord":
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})


# ==================================================================
# Regra de transferência por ativo
# ==================================================================
@dataclass
class TransferRule:
    require_kyc_sender: bool = True
    require_kyc_receiver: bool = True
    min_kyc_level: str = "basic"
    allowed_jurisdictions: List[str] = field(default_factory=list)
    blocked_jurisdictions: List[str] = field(default_factory=list)
    require_same_jurisdiction: bool = False
    max_holding_per_address: float = 0.0
    lockup_until: float = 0.0
    min_transfer_amount: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TransferRule":
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})


# ==================================================================
# Registro de ativos
# ==================================================================
class AssetRegistry:
    """Thread-safe. NÃO guarda saldos (isso é do State da blockchain)."""

    def __init__(self):
        self.assets: Dict[str, AssetDefinition] = {}
        self.compliance: Dict[str, ComplianceRecord] = {}
        self.rules: Dict[str, TransferRule] = {}
        self.regulator: str = ""
        self._lock = threading.RLock()

    # --------------------------------------------------------------
    # Ativos
    # --------------------------------------------------------------
    def add_asset(self, asset: AssetDefinition) -> tuple[bool, str]:
        with self._lock:
            if asset.asset_id in self.assets:
                return False, "asset_id já existe"
            if not _ASSET_ID_RE.match(asset.asset_id):
                return False, "asset_id inválido (A-Z0-9._-, 1-16)"
            if asset.asset_type not in ASSET_TYPES:
                return False, "asset_type inválido"
            if not isinstance(asset.decimals, int) or not (0 <= asset.decimals <= 18):
                return False, "decimals fora de 0..18"
            if asset.max_supply < 0:
                return False, "max_supply negativo"
            if not _valid_address(asset.issuer):
                return False, "issuer inválido"
            if not _valid_address(asset.transfer_agent):
                return False, "transfer_agent inválido"
            if asset.regulatory_status not in _REGULATORY_STATUS:
                return False, "regulatory_status inválido"

            self.assets[asset.asset_id] = asset
            if asset.asset_id not in self.rules:
                self.rules[asset.asset_id] = TransferRule()
            return True, "ok"

    def update_asset(self, asset_id: str, updates: dict) -> tuple[bool, str]:
        with self._lock:
            a = self.assets.get(asset_id)
            if not a:
                return False, "ativo não existe"
            bad = set(updates) - _MUTABLE_ASSET_FIELDS
            if bad:
                return False, f"campos não mutáveis: {sorted(bad)}"
            if "max_supply" in updates and float(updates["max_supply"]) < 0:
                return False, "max_supply negativo"
            if "transfer_agent" in updates and not _valid_address(updates["transfer_agent"]):
                return False, "transfer_agent inválido"
            for k, v in updates.items():
                setattr(a, k, v)
            return True, "ok"

    def get_asset(self, asset_id: str) -> Optional[AssetDefinition]:
        with self._lock:
            return self.assets.get(asset_id)

    def list_assets(self) -> List[AssetDefinition]:
        with self._lock:
            return list(self.assets.values())

    # --------------------------------------------------------------
    # Compliance
    # --------------------------------------------------------------
    def set_compliance(self, rec: ComplianceRecord) -> tuple[bool, str]:
        with self._lock:
            if rec.status not in KYC_STATUS:
                return False, "status inválido"
            if rec.level not in KYC_LEVELS:
                return False, "level inválido"
            if not _valid_address(rec.address):
                return False, "address inválido"
            for s in rec.sanctions:
                if s not in SANCTION_LISTS:
                    return False, f"sanção desconhecida: {s}"
            self.compliance[rec.address] = rec
            return True, "ok"

    def get_compliance(self, addr: str) -> Optional[ComplianceRecord]:
        with self._lock:
            return self.compliance.get(addr)

    def list_compliance(self, status: Optional[str] = None) -> List[ComplianceRecord]:
        with self._lock:
            if status is None:
                return list(self.compliance.values())
            return [r for r in self.compliance.values() if r.status == status]

    def list_sanctioned(self) -> List[ComplianceRecord]:
        return [r for r in self.list_compliance() if r.is_sanctioned()]

    def is_kyc_valid(self, addr: str, min_level: str = "basic") -> bool:
        with self._lock:
            rec = self.compliance.get(addr)
            if not rec or not rec.is_valid():
                return False
            try:
                return _LEVEL_ORDER[rec.level] >= _LEVEL_ORDER[min_level]
            except KeyError:
                return False

    def is_sanctioned(self, addr: str) -> bool:
        with self._lock:
            rec = self.compliance.get(addr)
            return bool(rec and rec.is_sanctioned())

    # --------------------------------------------------------------
    # Regras
    # --------------------------------------------------------------
    def set_rule(self, asset_id: str, rule: TransferRule) -> tuple[bool, str]:
        with self._lock:
            if asset_id not in self.assets:
                return False, "ativo não existe"
            if rule.min_kyc_level not in KYC_LEVELS:
                return False, "min_kyc_level inválido"
            self.rules[asset_id] = rule
            return True, "ok"

    def get_rule(self, asset_id: str) -> TransferRule:
        with self._lock:
            return self.rules.get(asset_id, TransferRule())

    # --------------------------------------------------------------
    # Permissões
    # --------------------------------------------------------------
    def can_issue(self, asset_id: str, addr: str) -> bool:
        with self._lock:
            a = self.assets.get(asset_id)
            return bool(a and a.issuer == addr)

    def can_manage_kyc(self, asset_id: str, addr: str) -> bool:
        with self._lock:
            a = self.assets.get(asset_id)
            if not a:
                return False
            if addr in (a.issuer, a.transfer_agent):
                return True
            return bool(self.regulator) and addr == self.regulator

    def can_freeze(self, asset_id: str, addr: str) -> bool:
        with self._lock:
            a = self.assets.get(asset_id)
            if not a:
                return False
            if addr in (a.issuer, a.transfer_agent):
                return True
            return bool(self.regulator) and addr == self.regulator

    # --------------------------------------------------------------
    # Validação de transferência
    # --------------------------------------------------------------
    def validate_transfer(self, asset_id: str, sender: str, receiver: str,
                          amount: float,
                          receiver_balance_after: float) -> tuple[bool, str]:
        with self._lock:
            a = self.assets.get(asset_id)
            if not a:
                return False, f"ativo '{asset_id}' não existe"
            if a.frozen:
                return False, "ativo congelado"
            if amount <= 0:
                return False, "valor deve ser positivo"

            s_rec = self.compliance.get(sender)
            r_rec = self.compliance.get(receiver)

            # ---------- sanções primeiro ----------
            if s_rec and s_rec.is_sanctioned():
                return False, "remetente sancionado"
            if r_rec and r_rec.is_sanctioned():
                return False, "destinatário sancionado"

            rule = self.get_rule(asset_id)
            if rule.lockup_until and time.time() < rule.lockup_until:
                return False, "lockup ativo"
            if amount < rule.min_transfer_amount:
                return False, "abaixo do mínimo"

            if a.transfer_restricted:
                if rule.require_kyc_sender and not self.is_kyc_valid(sender, rule.min_kyc_level):
                    return False, "remetente sem KYC válido"
                if rule.require_kyc_receiver and not self.is_kyc_valid(receiver, rule.min_kyc_level):
                    return False, "destinatário sem KYC válido"

                if s_rec:
                    if rule.allowed_jurisdictions and s_rec.jurisdiction not in rule.allowed_jurisdictions:
                        return False, "jurisdição do remetente não permitida"
                    if s_rec.jurisdiction in rule.blocked_jurisdictions:
                        return False, "jurisdição do remetente bloqueada"

                if r_rec:
                    if rule.allowed_jurisdictions and r_rec.jurisdiction not in rule.allowed_jurisdictions:
                        return False, "jurisdição do destinatário não permitida"
                    if r_rec.jurisdiction in rule.blocked_jurisdictions:
                        return False, "jurisdição do destinatário bloqueada"
                    if "block_transfer" in r_rec.restrictions:
                        return False, "destinatário com restrição"

                if rule.require_same_jurisdiction:
                    if not (s_rec and r_rec and s_rec.jurisdiction == r_rec.jurisdiction):
                        return False, "mesma jurisdição exigida"

            if rule.max_holding_per_address > 0 and \
               receiver_balance_after > rule.max_holding_per_address:
                return False, "excederia o máximo do endereço"

            return True, "ok"

    # --------------------------------------------------------------
    # Serialização
    # --------------------------------------------------------------
    def to_dict(self) -> dict:
        with self._lock:
            return {
                "assets": {k: v.to_dict() for k, v in self.assets.items()},
                "compliance": {k: v.to_dict() for k, v in self.compliance.items()},
                "rules": {k: v.to_dict() for k, v in self.rules.items()},
                "regulator": self.regulator,
            }

    @classmethod
    def from_dict(cls, d: dict) -> "AssetRegistry":
        r = cls()
        r.regulator = d.get("regulator", "")
        for k, v in d.get("assets", {}).items():
            try:
                r.assets[k] = AssetDefinition.from_dict(v)
            except Exception:
                continue
        for k, v in d.get("compliance", {}).items():
            try:
                r.compliance[k] = ComplianceRecord.from_dict(v)
            except Exception:
                continue
        for k, v in d.get("rules", {}).items():
            try:
                r.rules[k] = TransferRule.from_dict(v)
            except Exception:
                continue
        return r