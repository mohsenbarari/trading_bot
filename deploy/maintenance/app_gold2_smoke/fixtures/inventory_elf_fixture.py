def elf(*,defined_helper=False,irrelevant=False):
 names=['','libacl.so.1','acl_get_file','perm_copy_fd','attr_copy_fd','perm_copy_fd_not_the_helper'];strings=b'\0';offsets={'':0}
 for n in names[1:]:offsets[n]=len(strings);strings+=n.encode()+b'\0'
 shnum=4;str_off=128;dyn_off=(str_off+len(strings)+7)//8*8;dynamic=b''.join(struct.pack('<qQ',k,v)for k,v in[(1,offsets['libacl.so.1']),(5,0x5000),(10,len(strings)),(6,0x6000),(11,24),(0,0)]);sym_off=(dyn_off+len(dynamic)+7)//8*8;symbols=bytes(24)
 for n in('acl_get_file','perm_copy_fd','attr_copy_fd','perm_copy_fd_not_the_helper'):
  symbols+=struct.pack('<IBBHQQ',offsets[n],0x12,0,1 if defined_helper and n in HN else 0,0,0)
 sh_off=(sym_off+len(symbols)+7)//8*8;data=bytearray(sh_off+4*64);data[:16]=b'\x7fELF\x02\x01\x01'+bytes(9);struct.pack_into('<HHIQQQIHHHHHH',data,16,3,62,1,0,64,sh_off,0,64,56,1,64,4,0);struct.pack_into('<IIQQQQQQ',data,64,2,4,dyn_off,0x7000,0,len(dynamic),len(dynamic),8);data[str_off:str_off+len(strings)]=strings;data[dyn_off:dyn_off+len(dynamic)]=dynamic;data[sym_off:sym_off+len(symbols)]=symbols
 for index,row in enumerate([(0,0,0,0,0,0,0,0,0,0),(0,3,0,0x5000,str_off,len(strings),0,0,1,0),(0,6,0,0x7000,dyn_off,len(dynamic),1,0,8,16),(0,11,0,0x6000,sym_off,len(symbols),1,0,8,24)]):struct.pack_into('<IIQQQQIIQQ',data,sh_off+index*64,*row)
 return bytes(data)
