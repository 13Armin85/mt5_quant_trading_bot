"""Inspect or click labelled buttons in a named MT5 dialog; never read edits."""
import argparse
import ctypes
import json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('title')
    parser.add_argument('--click')
    parser.add_argument('--uncheck')
    parser.add_argument('--list-menu',action='store_true')
    parser.add_argument('--menu')
    args=parser.parse_args()
    user=ctypes.windll.user32
    handle=ctypes.c_void_p
    user.FindWindowW.restype=handle
    user.SendMessageW.argtypes=[handle,ctypes.c_uint,ctypes.c_size_t,ctypes.c_ssize_t]
    user.SendMessageW.restype=ctypes.c_ssize_t
    parent=user.FindWindowW(None,args.title)
    if not parent:
        raise SystemExit('Named dialog is not open')
    if args.list_menu or args.menu:
        user.GetMenu.restype=handle
        user.GetSubMenu.restype=handle
        user.GetSubMenu.argtypes=[handle,ctypes.c_int]
        user.GetMenuItemID.argtypes=[handle,ctypes.c_int]
        user.GetMenuItemCount.argtypes=[handle]
        user.GetMenuStringW.argtypes=[handle,ctypes.c_uint,ctypes.c_wchar_p,ctypes.c_int,ctypes.c_uint]
        top=user.GetMenu(handle(parent))
        submenu=user.GetSubMenu(top,0)
        for i in range(user.GetMenuItemCount(submenu)):
            label=ctypes.create_unicode_buffer(256)
            user.GetMenuStringW(submenu,i,label,256,0x400)
            ident=user.GetMenuItemID(submenu,i)
            print(json.dumps({'menu':label.value,'id':ident}),flush=True)
            if args.menu and label.value.replace('&','').split('\t')[0]==args.menu:
                user.SendMessageW(parent,0x111,ident,0)
                print('Menu action requested',flush=True)
                return
        if args.menu:
            raise SystemExit('Named menu item not found')
        return
    found=[]
    callback=ctypes.WINFUNCTYPE(ctypes.c_bool,handle,ctypes.c_ssize_t)
    @callback
    def visit(child,_):
        name=ctypes.create_unicode_buffer(256)
        label=ctypes.create_unicode_buffer(1024)
        user.GetClassNameW(child,name,256)
        if name.value.lower() not in {'button','static'}:
            return True
        user.GetWindowTextW(child,label,1024)
        if label.value and len(label.value)<180:
            item={'class':name.value,'label':label.value}
            if name.value.lower()=='button':
                item['checked']=user.SendMessageW(child,0xf0,0,0)
                if args.click and label.value.replace('&','')==args.click:
                    found.append(child)
                if args.uncheck and label.value.replace('&','')==args.uncheck:
                    found.append(child)
            print(json.dumps(item,ensure_ascii=True),flush=True)
        return True
    user.EnumChildWindows(parent,visit,0)
    if args.click or args.uncheck:
        if len(found)!=1:
            raise SystemExit('Exactly one matching button is required')
        if args.uncheck:
            # BM_CLICK sends the checkbox's normal change notification. Only
            # click when checked, so this operation can only disable it.
            if user.SendMessageW(found[0],0xf0,0,0)==1:
                user.SendMessageW(found[0],0xf5,0,0)
            if user.SendMessageW(found[0],0xf0,0,0)!=0:
                raise SystemExit('Checkbox did not become unchecked')
            print('Named checkbox explicitly disabled',flush=True)
        else:
            user.SendMessageW(found[0],0xf5,0,0)
            print('Named button clicked',flush=True)


if __name__=='__main__':
    main()
