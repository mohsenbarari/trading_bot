import enum

class UserRole(str, enum.Enum):
    WATCH = "تماشا"
    STANDARD = "عادی"
    POLICE = "پلیس"
    MIDDLE_MANAGER = "مدیر میانی"
    SUPER_ADMIN = "مدیر ارشد"