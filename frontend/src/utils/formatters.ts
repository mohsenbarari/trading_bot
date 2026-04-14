const DELETED_KEYS = new Set([
  'account_name',
  'mobile_number',
  'full_name',
  'sender_name',
  'receiver_name',
  'other_user_name',
  'offer_user_name',
  'responder_user_name',
  'trade_user_name',
  'name', // Used in some UI mapping places
]);

export function cleanDeletedSuffixes(obj: any): any {
  if (Array.isArray(obj)) {
    return obj.map(cleanDeletedSuffixes);
  } else if (obj !== null && typeof obj === 'object') {
    const newObj: any = {};
    for (const key in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, key)) {
        const val = obj[key];
        if (typeof val === 'string' && DELETED_KEYS.has(key)) {
          // Replace _del_ followed by digits at the end of the string
          newObj[key] = val.replace(/_del_\d+$/, '');
        } else {
          newObj[key] = cleanDeletedSuffixes(val);
        }
      }
    }
    return newObj;
  }
  return obj;
}